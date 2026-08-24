"""Making a week of history, so nothing starts empty.

Without this, every dashboard would be blank until the services had been
running for a day, and the traffic factors that routing depends on would
have nothing to start from. So bootstrap invents a believable week that ends
right now.

Two honest simplifications, both deliberate:

1. **History does not use pgRouting.** Routing 14,000 trips would take
   longer than the rest of bootstrap put together. Historical trips use the
   straight-line distance multiplied by a road factor, which is close enough
   for a chart. Live trips, from `dispatch-service` onwards, are routed for
   real.
2. **Position reports are thinned out.** A real device reports every few
   seconds; a week of that would be tens of millions of rows nobody reads
   closely. A handful of points per trip keeps the shape of the data without
   the weight.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from nus_common import postgres
from nus_common.geo import day_period, distance_km
from nus_common.logging import get_logger

from bootstrap import people, zones
from bootstrap.settings import Settings

log = get_logger(__name__)

# How busy each hour of the day is, relative to the others. Two peaks: people
# going to work and people going home, with a smaller late-evening bump.
HOUR_WEIGHTS = [
    0.3, 0.2, 0.15, 0.15, 0.2, 0.4,   # 00-05 night
    0.9, 1.6, 2.0, 1.4, 0.9, 0.9,     # 06-11 morning peak
    1.0, 1.0, 0.9, 1.0, 1.4, 2.0,     # 12-17 afternoon into evening peak
    1.7, 1.2, 1.0, 0.9, 0.8, 0.5,     # 18-23 evening
]

# How trips end. Roughly seven in ten finish; the rest fall away for the
# three reasons the platform knows about (main README, section 2.1).
OUTCOME_WEIGHTS = {
    "completed": 0.70,
    "cancelled_by_passenger": 0.12,
    "cancelled_by_driver": 0.10,
    "no_driver_found": 0.08,
}

# A straight line is shorter than a drive. Roads bend, and one-way streets
# and rivers make it worse in a city like this one.
ROAD_FACTOR = 1.4
# Average city speed in kilometres per hour, before congestion is applied.
FREE_FLOW_KMH = 26.0


@dataclass
class GeneratedWeek:
    """Everything one generated day produced, ready to be stored."""

    trip_rows: list[dict] = field(default_factory=list)
    trip_events: list[list] = field(default_factory=list)
    driver_positions: list[list] = field(default_factory=list)
    rider_positions: list[list] = field(default_factory=list)
    hotspots: list[list] = field(default_factory=list)


def _pick_hour(rng: random.Random) -> int:
    """Choose an hour of the day, with the busy hours more likely."""
    return rng.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]


def _zone_pull(zone_ids: list[str], rng: random.Random) -> dict[str, float]:
    """Give each zone a fixed popularity, so demand is uneven but stable.

    Without this every zone would be equally busy and there would be no such
    thing as a hotspot.
    """
    return {zid: rng.triangular(0.4, 2.5, 0.9) for zid in zone_ids}


def _surge_from_score(score: float) -> float:
    """Turn a demand score into a price multiplier.

    Stays at 1.0 until demand is clearly above normal, then rises gently and
    stops at 2.5 - a price that keeps climbing is a bug, not a feature.
    """
    if score <= 0.6:
        return 1.0
    return round(min(1.0 + (score - 0.6) * 1.6, 2.5), 2)


def generate(settings: Settings, seed_value: int = 20250824) -> GeneratedWeek:
    """Invent the whole week and return it, ready to be written."""
    rng = random.Random(seed_value)
    zone_ids = zones.all_zone_ids(settings)
    pull = _zone_pull(zone_ids, rng)
    weights = [pull[zid] for zid in zone_ids]

    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    week = GeneratedWeek()

    for day_offset in range(settings.history_days, 0, -1):
        day_start = now - timedelta(days=day_offset)
        for _ in range(settings.trips_per_day):
            _one_trip(settings, rng, zone_ids, weights, day_start, week)

    _hotspot_history(settings, rng, zone_ids, pull, now, week)

    log.info(
        "history generated",
        extra={
            "trips": len(week.trip_rows),
            "trip_events": len(week.trip_events),
            "driver_positions": len(week.driver_positions),
            "rider_positions": len(week.rider_positions),
            "hotspot_rows": len(week.hotspots),
        },
    )
    return week


def _one_trip(
    settings: Settings,
    rng: random.Random,
    zone_ids: list[str],
    weights: list[float],
    day_start: datetime,
    week: GeneratedWeek,
) -> None:
    """Invent one trip and add everything it produced to the week."""
    hour = _pick_hour(rng)
    requested_at = day_start + timedelta(
        hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
    )

    pickup_zone = rng.choices(zone_ids, weights=weights, k=1)[0]
    dropoff_zone = rng.choices(zone_ids, weights=weights, k=1)[0]
    pickup_lat, pickup_lon = zones.random_point_in_zone(settings, pickup_zone, rng)
    dropoff_lat, dropoff_lon = zones.random_point_in_zone(settings, dropoff_zone, rng)

    trip_id = f"trp-{requested_at.strftime('%Y%m%d')}-{rng.getrandbits(32):08x}"
    rider = people.passenger_id(rng.randint(1, settings.passenger_count))
    outcome = rng.choices(
        list(OUTCOME_WEIGHTS), weights=list(OUTCOME_WEIGHTS.values()), k=1
    )[0]

    # Nobody was found, so there is no driver, no route and no fare. The trip
    # is still recorded: "we could not serve this" is a number worth having.
    if outcome == "no_driver_found":
        week.trip_rows.append(
            _trip_row(
                trip_id=trip_id, rider=rider, driver=None, status=outcome,
                pickup=(pickup_lat, pickup_lon), dropoff=(dropoff_lat, dropoff_lon),
                pickup_zone=pickup_zone, dropoff_zone=dropoff_zone,
                route_km=None, predicted_s=None, actual_s=None,
                surge=None, estimate=None, final=None,
                requested_at=requested_at, ended_at=requested_at + timedelta(minutes=3),
            )
        )
        week.trip_events.append(
            _event_row(trip_id, rider, None, outcome, pickup_zone, None, None, None,
                       None, None, None, requested_at + timedelta(minutes=3))
        )
        return

    driver = people.driver_id(rng.randint(1, settings.driver_count))
    straight_km = distance_km(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon)
    route_km = round(straight_km * ROAD_FACTOR, 3)

    # Busy hours are slower hours. This is the same idea the live traffic
    # factors carry, applied to a whole hour at once.
    congestion = 1.0 + (HOUR_WEIGHTS[hour] - 1.0) * 0.35
    predicted_s = max(int(route_km / FREE_FLOW_KMH * 3600 * congestion), 120)
    surge = _surge_from_score(min(HOUR_WEIGHTS[hour] / 2.0, 1.2))
    estimate = round(
        (settings.base_fare + settings.per_km * route_km
         + settings.per_minute * predicted_s / 60) * surge,
        2,
    )

    if outcome != "completed":
        # Cancelled after matching: there is a driver and a quote, but no
        # journey and no charge.
        ended = requested_at + timedelta(minutes=rng.randint(1, 6))
        week.trip_rows.append(
            _trip_row(
                trip_id=trip_id, rider=rider, driver=driver, status=outcome,
                pickup=(pickup_lat, pickup_lon), dropoff=(dropoff_lat, dropoff_lon),
                pickup_zone=pickup_zone, dropoff_zone=dropoff_zone,
                route_km=route_km, predicted_s=predicted_s, actual_s=None,
                surge=surge, estimate=estimate, final=None,
                requested_at=requested_at, ended_at=ended,
            )
        )
        week.trip_events.append(
            _event_row(trip_id, rider, driver, outcome, pickup_zone, route_km,
                       predicted_s, None, surge, estimate, None, ended)
        )
        return

    # A completed trip. The real duration drifts from the prediction, which is
    # the whole point of storing both.
    actual_s = max(int(predicted_s * rng.triangular(0.75, 1.6, 1.05)), 120)
    started_at = requested_at + timedelta(minutes=rng.randint(2, 8))
    ended_at = started_at + timedelta(seconds=actual_s)
    final = round(
        (settings.base_fare + settings.per_km * route_km
         + settings.per_minute * actual_s / 60) * surge,
        2,
    )

    week.trip_rows.append(
        _trip_row(
            trip_id=trip_id, rider=rider, driver=driver, status="completed",
            pickup=(pickup_lat, pickup_lon), dropoff=(dropoff_lat, dropoff_lon),
            pickup_zone=pickup_zone, dropoff_zone=dropoff_zone,
            route_km=route_km, predicted_s=predicted_s, actual_s=actual_s,
            surge=surge, estimate=estimate, final=final,
            requested_at=requested_at, ended_at=ended_at, started_at=started_at,
        )
    )
    week.trip_events.append(
        _event_row(trip_id, rider, driver, "completed", pickup_zone, route_km,
                   predicted_s, actual_s, surge, estimate, final, ended_at)
    )

    # A few positions along the way, spread evenly between the two ends.
    steps = settings.positions_per_trip
    for step in range(steps):
        share = step / max(steps - 1, 1)
        lat = pickup_lat + (dropoff_lat - pickup_lat) * share
        lon = pickup_lon + (dropoff_lon - pickup_lon) * share
        moment = started_at + timedelta(seconds=int(actual_s * share))
        week.driver_positions.append(
            [driver, trip_id, "on_trip", lat, lon,
             float(rng.uniform(0, 360)), float(route_km / (actual_s / 3600) if actual_s else 0),
             pickup_zone, moment]
        )
        # The rider's phone reports less often and less precisely.
        if step % 2 == 0:
            week.rider_positions.append(
                [rider, trip_id, lat, lon, float(rng.uniform(4, 40)), pickup_zone, moment]
            )


def _hotspot_history(
    settings: Settings,
    rng: random.Random,
    zone_ids: list[str],
    pull: dict[str, float],
    now: datetime,
    week: GeneratedWeek,
) -> None:
    """One demand score per zone per hour of the week."""
    for hours_ago in range(settings.history_days * 24, 0, -1):
        moment = now - timedelta(hours=hours_ago)
        period = day_period(moment)
        hour_weight = HOUR_WEIGHTS[moment.hour]
        for zid in zone_ids:
            score = round(min(pull[zid] * hour_weight / 2.5, 1.0) * rng.uniform(0.8, 1.2), 3)
            score = min(score, 1.0)
            waiting = int(score * rng.randint(5, 40))
            free = max(int((1.05 - score) * rng.randint(5, 40)), 0)
            week.hotspots.append(
                [zid, period, score, waiting, free, _surge_from_score(score), moment]
            )


def _trip_row(**kwargs) -> dict:
    """One row for the PostgreSQL trips table."""
    pickup_lat, pickup_lon = kwargs["pickup"]
    dropoff_lat, dropoff_lon = kwargs["dropoff"]
    return {
        "trip_id": kwargs["trip_id"],
        "rider_id": kwargs["rider"],
        "driver_id": kwargs["driver"],
        "status": kwargs["status"],
        "pickup_lat": pickup_lat, "pickup_lon": pickup_lon,
        "dropoff_lat": dropoff_lat, "dropoff_lon": dropoff_lon,
        "pickup_zone_id": kwargs["pickup_zone"],
        "dropoff_zone_id": kwargs["dropoff_zone"],
        "route_km": kwargs["route_km"],
        "predicted_duration_s": kwargs["predicted_s"],
        "actual_duration_s": kwargs["actual_s"],
        "surge_multiplier": kwargs["surge"],
        "fare_estimate": kwargs["estimate"],
        "fare_final": kwargs["final"],
        "requested_at": kwargs["requested_at"],
        "started_at": kwargs.get("started_at"),
        "ended_at": kwargs["ended_at"],
    }


def _event_row(trip_id, rider, driver, status, zone, route_km, predicted_s,
               actual_s, surge, estimate, final, moment) -> list:
    """One row for the ClickHouse trip_events table.

    The column order matches warehouse.TRIP_EVENT_COLUMNS.
    """
    delta = None if (actual_s is None or predicted_s is None) else actual_s - predicted_s
    longer = None if delta is None else int(delta > 0)
    return [
        trip_id, rider, driver, status, zone,
        route_km, predicted_s, actual_s, delta, longer,
        surge, None, None, estimate, final, moment,
    ]


def store_trips(rows: list[dict], batch_size: int = 1000) -> int:
    """Write the generated trips into PostgreSQL.

    Written in batches so one long transaction does not hold the leader for
    the whole run, and so a failure shows which batch it happened in.
    """
    inserted = 0
    sql = """
        INSERT INTO trips (
            trip_id, rider_id, driver_id, status,
            pickup_point, dropoff_point, pickup_zone_id, dropoff_zone_id,
            route_km, predicted_duration_s, actual_duration_s,
            surge_multiplier, fare_estimate, fare_final,
            requested_at, started_at, ended_at
        )
        VALUES (
            %(trip_id)s, %(rider_id)s, %(driver_id)s, %(status)s,
            ST_SetSRID(ST_MakePoint(%(pickup_lon)s, %(pickup_lat)s), 4326),
            ST_SetSRID(ST_MakePoint(%(dropoff_lon)s, %(dropoff_lat)s), 4326),
            %(pickup_zone_id)s, %(dropoff_zone_id)s,
            %(route_km)s, %(predicted_duration_s)s, %(actual_duration_s)s,
            %(surge_multiplier)s, %(fare_estimate)s, %(fare_final)s,
            %(requested_at)s, %(started_at)s, %(ended_at)s
        )
        ON CONFLICT (trip_id) DO NOTHING
    """
    with postgres.write_connection() as conn:
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            with conn.cursor() as cur:
                cur.executemany(sql, batch)
            conn.commit()
            inserted += len(batch)
            log.info("trips written", extra={"done": inserted, "of": len(rows)})
    return inserted


def seed_segment_traffic(sample_size: int = 20000, seed_value: int = 20250824) -> int:
    """Give every sampled road segment a starting congestion factor.

    Routing needs a number here from the very first trip, and city-service
    only starts refining it once live traffic exists. Segments are sampled
    rather than filled in completely: the map has hundreds of thousands of
    them, most of which will never carry a simulated trip.
    """
    rng = random.Random(seed_value)
    periods = ("night", "morning", "afternoon", "evening")
    # Mornings and evenings are slower; the small night number means faster
    # than free flow, which is what an empty city really is.
    base = {"night": 0.9, "morning": 1.35, "afternoon": 1.15, "evening": 1.4}

    with postgres.write_connection() as conn:
        rows = postgres.fetch_all(
            conn,
            "SELECT gid FROM ways ORDER BY random() LIMIT %s",
            (sample_size,),
        )
        if not rows:
            log.warning("no road segments found - was the map imported?")
            return 0

        values = [
            {
                "way_id": row["gid"],
                "period": period,
                "factor": round(base[period] * rng.uniform(0.85, 1.25), 3),
            }
            for row in rows
            for period in periods
        ]

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO segment_traffic (way_id, period, congestion_factor, sample_count)
                VALUES (%(way_id)s, %(period)s, %(factor)s, 1)
                ON CONFLICT (way_id, period) DO NOTHING
                """,
                values,
            )
        conn.commit()

    log.info("traffic baseline ready", extra={"rows": len(values)})
    return len(values)
