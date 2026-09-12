"""Making a week of history, so nothing starts empty.

Without this, every dashboard would be blank until the services had been
running for a day, and the traffic factors that routing depends on would
have nothing to start from. So bootstrap invents a believable week that ends
right now.

Historical trips are routed for real, through nus_common.routing.route() -
the same pgRouting query dispatch-service uses for live trips. A pickup and
dropoff picked at random are never priced until the router has confirmed
they are actually connected by a real road; when they are not (a point in
open water, a road not in the imported area), the trip is recorded as
no_driver_found instead of priced across nothing. Position reports then
follow that real route rather than a straight line between the two ends.

Routing is a Postgres round trip - the most expensive step in the pipeline,
per nus_common.routing's own docstring - so it runs on a small pool of
worker threads (HISTORY_ROUTING_WORKERS) instead of one trip at a time.
Everything that has to stay reproducible for a given seed (which zones, which
ids, which outcome, how much jitter) is decided sequentially, before the
threaded pass starts; only the network call itself runs concurrently, and
results are matched back up with their trip in the original order.

One honest simplification remains: position reports are thinned out. A real
device reports every few seconds; a week of that would be tens of millions
of rows nobody reads closely. A handful of points per trip, spaced along the
real route, keeps the shape of the data without the weight.

If the street map was not imported (SKIP_MAP_IMPORT=true, for fast local
iteration), there is nothing to route against - trips fall back to the old
straight-line-times-a-road-factor estimate instead, clearly degraded and
logged as such.
"""

import random
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psycopg

from nus_common import demand_calibration, postgres, redis_client, routing
from nus_common.geo import day_period, distance_km, points_along_linestring
from nus_common.ids import new_trip_id
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

# How trips end, before routing gets a say. Roughly seven in ten finish; the
# rest fall away for the three reasons the platform knows about (main
# README, section 2.1). A "completed" or "cancelled" draw that turns out to
# have no real route between its two points still ends up no_driver_found -
# the real dispatch-service can't quote, match, or cancel a trip it never
# managed to route in the first place, so neither does this.
OUTCOME_WEIGHTS = {
    "completed": 0.70,
    "cancelled_by_passenger": 0.12,
    "cancelled_by_driver": 0.10,
    "no_driver_found": 0.08,
}

# Same distribution passenger-service draws requests from at runtime
# (k-service-passenger/passenger_service/__main__.py's VEHICLE_TYPE_WEIGHTS)
# - the historical week and live traffic should look like the same city.
VEHICLE_TYPE_WEIGHTS = [70, 20, 10]

# Only used when the street map was not imported. A straight line is shorter
# than a drive; roads bend, and one-way streets and rivers make it worse in a
# city like this one.
ROAD_FACTOR = 1.4
# Average city speed in kilometres per hour, before congestion is applied.
# Also fallback-only.
FREE_FLOW_KMH = 26.0


@dataclass
class GeneratedWeek:
    """Everything one generated day produced, ready to be stored."""

    trip_rows: list[dict] = field(default_factory=list)
    trip_ratings: list[dict] = field(default_factory=list)
    trip_events: list[list] = field(default_factory=list)
    driver_positions: list[list] = field(default_factory=list)
    rider_positions: list[list] = field(default_factory=list)
    hotspots: list[list] = field(default_factory=list)


@dataclass
class _TripSpec:
    """Everything about one trip decided before routing - reproducible for a
    given seed, independent of whether pgRouting finds a path."""

    trip_id: str
    rider: str
    driver: str | None
    outcome: str
    hour: int
    period: str
    pickup_zone: str
    dropoff_zone: str
    pickup_lat: float
    pickup_lon: float
    dropoff_lat: float
    dropoff_lon: float
    requested_vehicle_type: str
    requested_at: datetime


def _pick_hour(rng: random.Random) -> int:
    """Choose an hour of the day, with the busy hours more likely."""
    return rng.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]


def _star(rng: random.Random) -> int:
    """A 1-5 rating. Most trips go fine, so most ratings cluster near the
    top - the same triangular shape people.py uses for a starting rating."""
    return round(rng.triangular(3, 5, 4.7))


def _surge_from_score(score: float) -> float:
    """Turn a demand score into a price multiplier.

    Stays at 1.0 until demand is clearly above normal, then rises gently and
    stops at 2.5 - a price that keeps climbing is a bug, not a feature.
    """
    if score <= 0.6:
        return 1.0
    return round(min(1.0 + (score - 0.6) * 1.6, 2.5), 2)


def _map_available() -> bool:
    """True when the street map has been imported and pgRouting can be used."""
    try:
        with postgres.read_connection() as conn:
            row = postgres.fetch_one(conn, "SELECT count(*) AS n FROM ways")
        return bool(row and row["n"])
    except psycopg.Error as err:
        log.debug("map not queryable yet", extra={"error": str(err)})
        return False


def generate(settings: Settings, seed_value: int = 20250824) -> GeneratedWeek:
    """Invent the whole week and return it, ready to be written."""
    # A second, separate rng from the ones the process pool workers use
    # below: this one is only for what still happens sequentially in this
    # process after routing (see _finish_trip/_hotspot_history) - a much
    # smaller amount of work, not worth parallelizing on its own.
    rng = random.Random(seed_value)
    # zones.all_zone_ids() (grid().all_zone_ids()), not
    # routing.servicable_zone_ids() - see people.py's own seed() for why:
    # two independent read-replica queries against the same "servicable"
    # condition can disagree under replication lag, and grid() is what
    # every point-picking call below actually uses, so deriving zone_ids
    # from it instead of a second query makes the two impossible to
    # disagree. By the time this runs, people.seed() has already loaded
    # and cached the grid - this is not a new database call.
    zone_ids = zones.all_zone_ids()
    # Force-loaded here, before any process pool below is created: a
    # forked worker inherits whatever is already cached at fork time, not
    # anything loaded afterward - the same reasoning as the road-point
    # pool above it. Without this, every worker's own first zone_weight()/
    # od_share() call would each independently query Postgres.
    demand_calibration.preload()

    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    week = GeneratedWeek()

    map_available = _map_available()
    if not map_available:
        log.warning(
            "street map not available - historical trips will use the "
            "straight-line fallback, not real routing"
        )

    # Deciding one trip's pickup/dropoff/outcome before routing is pure
    # CPU work (zone-weight and OD-share lookups, distance decay - a
    # handful of per-zone loops, run once per trip) - confirmed live to
    # be a real, severe bottleneck at full scale (up to
    # history_days x trips_per_day trips, each doing several O(zones)
    # passes), the same class of problem people.py's Faker generation
    # had. Real processes, not threads, for the same reason: this is
    # CPU-bound, not I/O-bound, so the GIL would serialize it across
    # threads regardless of how many were started.
    workers = settings.history_generation_workers
    to_route: list[_TripSpec] = []
    for day_offset in range(settings.history_days, 0, -1):
        day_start = now - timedelta(days=day_offset)
        chunk_args = [
            (settings, seed_value, zone_ids, day_start, day_offset, start, end)
            for start, end in _chunks(settings.trips_per_day, workers)
        ]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for chunk_to_route, chunk_rows, chunk_events in pool.map(_spec_chunk, chunk_args):
                to_route.extend(chunk_to_route)
                week.trip_rows.extend(chunk_rows)
                week.trip_events.extend(chunk_events)

    if map_available:
        with ThreadPoolExecutor(max_workers=settings.history_routing_workers) as pool:
            routed = list(
                pool.map(
                    lambda s: routing.route(
                        s.pickup_lat, s.pickup_lon, s.dropoff_lat, s.dropoff_lon, s.period
                    ),
                    to_route,
                )
            )
    else:
        routed = [None] * len(to_route)

    for spec, computed in zip(to_route, routed):
        if computed is not None:
            route_km, predicted_s, route_wkt = computed
        elif map_available:
            # Routed for real, but the two points are not connected in the
            # imported map - the same outcome dispatch-service gives a trip
            # it cannot route live.
            _finish_no_driver(spec, week)
            continue
        else:
            route_km, predicted_s, route_wkt = _fallback_route(spec)

        _finish_trip(spec, route_km, predicted_s, route_wkt, rng, settings, week)

    _hotspot_history(settings, rng, zone_ids, now, week)

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


def _chunks(total: int, workers: int) -> list[tuple[int, int]]:
    """Split the range 0..total into up to `workers` contiguous pieces."""
    if total <= 0:
        return []
    workers = max(1, min(workers, total))
    size = -(-total // workers)  # ceiling division, no float rounding
    return [(start, min(start + size, total)) for start in range(0, total, size)]


def _spec_chunk(
    args: tuple[Settings, int, list[str], datetime, int, int, int],
) -> tuple[list["_TripSpec"], list[dict], list[list]]:
    """Decide one contiguous range of one day's trips - runs in its own
    process (see generate() above for why).

    Its own random.Random, seeded from the day and the chunk's own start
    index rather than shared: independent of every other chunk, and of
    how many workers there are, so the same seed_value and worker count
    always reproduce the same week (a different worker count changes the
    specific trips generated, the same way a different HISTORY_DAYS
    would - the guarantee is "these settings always produce this week",
    not "the exact trips a different partitioning would have produced").

    Returns (specs still needing routing, no_driver trip rows, no_driver
    trip events) - plain, picklable data, not a GeneratedWeek: a worker
    process cannot mutate the parent's own week object directly.
    """
    settings, seed_value, zone_ids, day_start, day_offset, start, end = args
    rng = random.Random(f"{seed_value}:history:{day_offset}:{start}")

    to_route: list[_TripSpec] = []
    no_driver_rows: list[dict] = []
    no_driver_events: list[list] = []
    for _ in range(start, end):
        spec = _next_spec(settings, rng, zone_ids, day_start)
        if spec.outcome == "no_driver_found":
            row, event = _no_driver_rows(spec)
            no_driver_rows.append(row)
            no_driver_events.append(event)
        else:
            to_route.append(spec)
    return to_route, no_driver_rows, no_driver_events


def _next_spec(
    settings: Settings,
    rng: random.Random,
    zone_ids: list[str],
    day_start: datetime,
) -> _TripSpec:
    """Decide everything about one trip that does not depend on routing."""
    hour = _pick_hour(rng)
    day_of_week = day_start.weekday()
    requested_at = day_start + timedelta(
        hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
    )

    # Real TLC-trip-record weight for this exact zone/hour/day-of-week
    # (nus_common.demand_calibration - see zone-demand-prepare), not a
    # flat per-zone guess: JFK at 8am on a Monday is genuinely busier than
    # a residential zone at 3am, and now that shows up here because it
    # showed up in a real month of trips.
    weights = [demand_calibration.zone_weight(zid, hour, day_of_week) for zid in zone_ids]
    pickup_zone = rng.choices(zone_ids, weights=weights, k=1)[0]

    # The dropoff is not picked independently of the pickup - most real trips
    # are short hops, with a long tail of longer ones, not a flat
    # distribution across the whole city. Real OD shares from the same
    # calibration month are used wherever they exist; distance decay is
    # the fallback for a pair the sample month never recorded, not a
    # replacement for it.
    grid = zones.grid()
    decay_weights = grid.distance_decay_weights(pickup_zone, zone_ids, weights)
    dropoff_weights = [
        demand_calibration.od_share(pickup_zone, zid) or decay_weights[i]
        for i, zid in enumerate(zone_ids)
    ]
    dropoff_zone = rng.choices(zone_ids, weights=dropoff_weights, k=1)[0]
    pickup_lat, pickup_lon = zones.pooled_road_point_in_zone(pickup_zone, rng)
    dropoff_lat, dropoff_lon = zones.pooled_road_point_in_zone(dropoff_zone, rng)

    trip_id = new_trip_id(requested_at, rng)
    vehicle_type = rng.choices(redis_client.VEHICLE_TYPES, VEHICLE_TYPE_WEIGHTS)[0]
    rider = people.passenger_id(rng.randint(1, settings.passenger_count))
    outcome = rng.choices(
        list(OUTCOME_WEIGHTS), weights=list(OUTCOME_WEIGHTS.values()), k=1
    )[0]
    driver = (
        None if outcome == "no_driver_found"
        else people.driver_id(rng.randint(1, settings.driver_count))
    )

    return _TripSpec(
        trip_id=trip_id, rider=rider, driver=driver, outcome=outcome,
        hour=hour, period=day_period(requested_at),
        pickup_zone=pickup_zone, dropoff_zone=dropoff_zone,
        pickup_lat=pickup_lat, pickup_lon=pickup_lon,
        dropoff_lat=dropoff_lat, dropoff_lon=dropoff_lon,
        requested_vehicle_type=vehicle_type,
        requested_at=requested_at,
    )


def _fallback_route(spec: _TripSpec) -> tuple[float, int, None]:
    """The pre-pgRouting estimate, used only when the map is not available."""
    straight_km = distance_km(
        spec.pickup_lat, spec.pickup_lon, spec.dropoff_lat, spec.dropoff_lon
    )
    route_km = round(straight_km * ROAD_FACTOR, 3)
    congestion = 1.0 + (HOUR_WEIGHTS[spec.hour] - 1.0) * 0.35
    predicted_s = max(int(route_km / FREE_FLOW_KMH * 3600 * congestion), 120)
    return route_km, predicted_s, None


def _no_driver_rows(spec: _TripSpec) -> tuple[dict, list]:
    """No route, no driver, no fare - the trip row and event row for that
    outcome, as plain data rather than appended to a GeneratedWeek
    directly, so a process pool worker can produce these too."""
    ended_at = spec.requested_at + timedelta(minutes=3)
    row = _trip_row(
        trip_id=spec.trip_id, rider=spec.rider, driver=None, status="no_driver_found",
        pickup=(spec.pickup_lat, spec.pickup_lon), dropoff=(spec.dropoff_lat, spec.dropoff_lon),
        pickup_zone=spec.pickup_zone, dropoff_zone=spec.dropoff_zone,
        requested_vehicle_type=spec.requested_vehicle_type,
        route_km=None, predicted_s=None, actual_s=None,
        surge=None, estimate=None, final=None,
        requested_at=spec.requested_at, ended_at=ended_at,
    )
    event = _event_row(spec.trip_id, spec.rider, None, "no_driver_found", spec.pickup_zone,
                        spec.dropoff_zone, None, None, None, None, None, None, ended_at)
    return row, event


def _finish_no_driver(spec: _TripSpec, week: GeneratedWeek) -> None:
    """No route, no driver, no fare. The trip is still recorded - "we could
    not serve this" is a number worth having."""
    row, event = _no_driver_rows(spec)
    week.trip_rows.append(row)
    week.trip_events.append(event)


def _finish_trip(
    spec: _TripSpec,
    route_km: float,
    predicted_s: int,
    route_wkt: str | None,
    rng: random.Random,
    settings: Settings,
    week: GeneratedWeek,
) -> None:
    """Turn a routed trip spec into trip/event/position rows."""
    surge = _surge_from_score(min(HOUR_WEIGHTS[spec.hour] / 2.0, 1.2))
    estimate = round(
        (settings.base_fare + settings.per_km * route_km
         + settings.per_minute * predicted_s / 60) * surge,
        2,
    )

    if spec.outcome != "completed":
        # Cancelled after matching: there is a driver and a quote, but no
        # journey and no charge. Same reason sets dispatch-service draws
        # from for live trips (l-service-dispatch/dispatch_service/
        # __main__.py's DRIVER_CANCEL_REASONS/PASSENGER_CANCEL_REASONS).
        ended = spec.requested_at + timedelta(minutes=rng.randint(1, 6))
        reason = (
            rng.choice(["rider_no_show", "driver_too_far", "vehicle_issue"])
            if spec.outcome == "cancelled_by_driver"
            else rng.choice(["changed_mind", "found_alternative", "wait_too_long"])
        )
        week.trip_rows.append(
            _trip_row(
                trip_id=spec.trip_id, rider=spec.rider, driver=spec.driver, status=spec.outcome,
                pickup=(spec.pickup_lat, spec.pickup_lon), dropoff=(spec.dropoff_lat, spec.dropoff_lon),
                pickup_zone=spec.pickup_zone, dropoff_zone=spec.dropoff_zone,
                requested_vehicle_type=spec.requested_vehicle_type,
                cancellation_reason=reason,
                route_km=route_km, route_wkt=route_wkt, predicted_s=predicted_s, actual_s=None,
                surge=surge, estimate=estimate, final=None,
                requested_at=spec.requested_at, ended_at=ended,
            )
        )
        week.trip_events.append(
            _event_row(spec.trip_id, spec.rider, spec.driver, spec.outcome, spec.pickup_zone,
                       spec.dropoff_zone, route_km, predicted_s, None, surge, estimate, None, ended)
        )
        return

    # A completed trip. The real duration drifts from the prediction, which is
    # the whole point of storing both.
    actual_s = max(int(predicted_s * rng.triangular(0.75, 1.6, 1.05)), 120)
    started_at = spec.requested_at + timedelta(minutes=rng.randint(2, 8))
    ended_at = started_at + timedelta(seconds=actual_s)
    final = round(
        (settings.base_fare + settings.per_km * route_km
         + settings.per_minute * actual_s / 60) * surge,
        2,
    )
    payout = round(final * (1 - settings.platform_commission_pct), 2)
    payment_method = rng.choice(["card", "wallet", "cash"])

    week.trip_rows.append(
        _trip_row(
            trip_id=spec.trip_id, rider=spec.rider, driver=spec.driver, status="completed",
            pickup=(spec.pickup_lat, spec.pickup_lon), dropoff=(spec.dropoff_lat, spec.dropoff_lon),
            pickup_zone=spec.pickup_zone, dropoff_zone=spec.dropoff_zone,
            requested_vehicle_type=spec.requested_vehicle_type,
            driver_payout=payout, payment_method=payment_method,
            route_km=route_km, route_wkt=route_wkt, predicted_s=predicted_s, actual_s=actual_s,
            surge=surge, estimate=estimate, final=final,
            requested_at=spec.requested_at, ended_at=ended_at, started_at=started_at,
        )
    )
    week.trip_events.append(
        _event_row(spec.trip_id, spec.rider, spec.driver, "completed", spec.pickup_zone,
                   spec.dropoff_zone, route_km, predicted_s, actual_s, surge, estimate, final, ended_at)
    )
    # Same bidirectional pattern dispatch-service uses for live trips
    # (l-service-dispatch/dispatch_service/ratings.py) - a completed trip
    # that never got rated would make drivers.rating/passengers.rating a lie
    # for every historically-seeded driver and rider.
    week.trip_ratings.append({"trip_id": spec.trip_id, "rater_type": "rider", "rating": _star(rng)})
    week.trip_ratings.append({"trip_id": spec.trip_id, "rater_type": "driver", "rating": _star(rng)})

    # A few positions along the way. When a real route exists they follow its
    # streets; otherwise (the map-unavailable fallback) they fall back to a
    # straight line between the two ends.
    steps = settings.positions_per_trip
    if route_wkt:
        path = points_along_linestring(route_wkt, steps)
    else:
        path = [
            (
                spec.pickup_lat + (spec.dropoff_lat - spec.pickup_lat) * step / max(steps - 1, 1),
                spec.pickup_lon + (spec.dropoff_lon - spec.pickup_lon) * step / max(steps - 1, 1),
            )
            for step in range(steps)
        ]

    for step, (lat, lon) in enumerate(path):
        share = step / max(steps - 1, 1)
        moment = started_at + timedelta(seconds=int(actual_s * share))
        week.driver_positions.append(
            [spec.driver, spec.trip_id, "on_trip", lat, lon,
             float(rng.uniform(0, 360)), float(route_km / (actual_s / 3600) if actual_s else 0),
             spec.pickup_zone, moment]
        )
        # The rider's phone reports less often and less precisely.
        if step % 2 == 0:
            week.rider_positions.append(
                [spec.rider, spec.trip_id, lat, lon, float(rng.uniform(4, 40)), spec.pickup_zone, moment]
            )


def _hotspot_history(
    settings: Settings,
    rng: random.Random,
    zone_ids: list[str],
    now: datetime,
    week: GeneratedWeek,
) -> None:
    """One demand score per zone per hour of the week."""
    for hours_ago in range(settings.history_days * 24, 0, -1):
        moment = now - timedelta(hours=hours_ago)
        period = day_period(moment)
        for zid in zone_ids:
            real_weight = demand_calibration.zone_weight(zid, moment.hour, moment.weekday())
            score = round(min(real_weight / 2.5, 1.0) * rng.uniform(0.8, 1.2), 3)
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
        "requested_vehicle_type": kwargs["requested_vehicle_type"],
        "cancellation_reason": kwargs.get("cancellation_reason"),
        "driver_payout": kwargs.get("driver_payout"),
        "payment_method": kwargs.get("payment_method"),
        "route_km": kwargs["route_km"],
        "route_wkt": kwargs.get("route_wkt"),
        "predicted_duration_s": kwargs["predicted_s"],
        "actual_duration_s": kwargs["actual_s"],
        "surge_multiplier": kwargs["surge"],
        "fare_estimate": kwargs["estimate"],
        "fare_final": kwargs["final"],
        "requested_at": kwargs["requested_at"],
        "started_at": kwargs.get("started_at"),
        "ended_at": kwargs["ended_at"],
    }


def _event_row(trip_id, rider, driver, status, zone, dropoff_zone, route_km,
               predicted_s, actual_s, surge, estimate, final, moment) -> list:
    """One row for the ClickHouse trip_events table.

    The column order matches warehouse.TRIP_EVENT_COLUMNS.
    """
    delta = None if (actual_s is None or predicted_s is None) else actual_s - predicted_s
    longer = None if delta is None else int(delta > 0)
    return [
        trip_id, rider, driver, status, zone, dropoff_zone,
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
            requested_vehicle_type, cancellation_reason,
            driver_payout, payment_method,
            route, route_km, predicted_duration_s, actual_duration_s,
            surge_multiplier, fare_estimate, fare_final,
            requested_at, started_at, ended_at
        )
        VALUES (
            %(trip_id)s, %(rider_id)s, %(driver_id)s, %(status)s,
            ST_SetSRID(ST_MakePoint(%(pickup_lon)s, %(pickup_lat)s), 4326),
            ST_SetSRID(ST_MakePoint(%(dropoff_lon)s, %(dropoff_lat)s), 4326),
            %(pickup_zone_id)s, %(dropoff_zone_id)s,
            %(requested_vehicle_type)s, %(cancellation_reason)s,
            %(driver_payout)s, %(payment_method)s,
            -- NULL for no_driver_found, and for the straight-line fallback
            -- when the map was not available - ST_GeomFromText(NULL, ...)
            -- is itself NULL, no CASE needed.
            ST_GeomFromText(%(route_wkt)s::text, 4326),
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


def store_trip_ratings(rows: list[dict], batch_size: int = 1000) -> int:
    """Write the historical week's ratings, then refresh every average once.

    The averages are recomputed set-wise, one UPDATE for all drivers and one
    for all passengers, after every row is in - not one UPDATE per trip the
    way dispatch-service's live path does it. Live trips complete a handful
    at a time; a week of history completes hundreds of thousands at once, so
    a per-trip round trip here would turn a few seconds of writing into the
    slowest part of the whole run.
    """
    inserted = 0
    sql = """
        INSERT INTO trip_ratings (trip_id, rater_type, rating)
        VALUES (%(trip_id)s, %(rater_type)s, %(rating)s)
        ON CONFLICT (trip_id, rater_type) DO NOTHING
    """
    with postgres.write_connection() as conn:
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            with conn.cursor() as cur:
                cur.executemany(sql, batch)
            conn.commit()
            inserted += len(batch)

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE drivers d SET rating = a.avg_rating
                  FROM (
                      SELECT t.driver_id, round(avg(tr.rating)::numeric, 1) AS avg_rating
                        FROM trip_ratings tr JOIN trips t ON t.trip_id = tr.trip_id
                       WHERE tr.rater_type = 'rider' AND t.driver_id IS NOT NULL
                       GROUP BY t.driver_id
                  ) a
                 WHERE a.driver_id = d.driver_id
            """)
            cur.execute("""
                UPDATE passengers p SET rating = a.avg_rating
                  FROM (
                      SELECT t.rider_id, round(avg(tr.rating)::numeric, 1) AS avg_rating
                        FROM trip_ratings tr JOIN trips t ON t.trip_id = tr.trip_id
                       WHERE tr.rater_type = 'driver'
                       GROUP BY t.rider_id
                  ) a
                 WHERE a.rider_id = p.passenger_id
            """)
        conn.commit()

    log.info("trip ratings written", extra={"ratings": inserted})
    return inserted


def seed_segment_traffic(sample_size: int = 20000, seed_value: int = 20250824) -> int:
    """Give every sampled road segment a starting congestion factor.

    Routing needs a number here from the very first trip - including the
    historical week's own trips, which is why this runs before generate()
    - and city-service only starts refining it once live traffic exists.
    Segments are sampled rather than filled in completely: the map has
    hundreds of thousands of them, most of which will never carry a
    simulated trip.
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
