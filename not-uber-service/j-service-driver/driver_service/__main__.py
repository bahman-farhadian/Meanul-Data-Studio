"""Run the simulated fleet.

One container, many drivers. Every few seconds the service does the same
four things:

  1. read any trip news, so a driver knows it has been given a trip;
  2. move every online driver a little;
  3. send one position report per online driver to Kafka;
  4. keep the list of free drivers in Redis up to date, so dispatch can find
     the nearest one.

The database is written to far less often than that. A position that changes
every three seconds does not belong in an OLTP table; Kafka carries the
stream, and PostgreSQL keeps the last known state, refreshed on a timer.
"""

import json
import random
import sys
import time

from nus_common import config, postgres, redis_client, routing
from nus_common.citygrid import CityGrid
from nus_common.geo import day_period, to_millis, utc_now
from nus_common.kafka import AvroTopicConsumer, AvroTopicProducer
from nus_common.lifecycle import Shutdown, wait_for, wait_for_bootstrap
from nus_common.logging import get_logger, setup_logging

from driver_service.fleet import (
    EN_ROUTE_PICKUP,
    IDLE,
    OFFLINE,
    ON_TRIP,
    Driver,
    pick_target_zone,
)

log = get_logger(__name__)

TOPIC = "driver_location"
LIFECYCLE_TOPIC = "trip_lifecycle"

# Which trip status puts a driver into which state.
STATUS_EFFECT = {
    "matched": EN_ROUTE_PICKUP,
    "accepted": EN_ROUTE_PICKUP,
    "en_route_pickup": EN_ROUTE_PICKUP,
    "in_progress": ON_TRIP,
    "completed": IDLE,
    "cancelled_by_passenger": IDLE,
    "cancelled_by_driver": IDLE,
    "no_driver_found": IDLE,
}


def load_roster(
    redis, grid: CityGrid, rng: random.Random, zone_ids: list[str]
) -> dict[str, Driver]:
    """Read the drivers out of Redis.

    Redis, not PostgreSQL: the profiles are in the cache because
    cache-updater put them there, and section 1 of the main README says the
    read path is the cache. If the cache is empty the service has started too
    early, and the caller is expected to wait and try again.
    """
    drivers: dict[str, Driver] = {}
    for key in redis.scan_iter(match="driver:*", count=500):
        raw = redis.get(key)
        if not raw:
            continue
        row = json.loads(raw)
        driver_id = row["driver_id"]
        # zone_ids, not grid.all_zone_ids(): a driver homed in a zone whose
        # own centroid cannot reach a real road would keep re-hitting the
        # unsnapped-point fallback forever. This is only the defensive
        # fallback for a profile with no home_zone_id at all - bootstrap
        # already homes every real driver in a servicable zone.
        home = row.get("home_zone_id") or rng.choice(zone_ids)
        lat = row.get("last_lat")
        lon = row.get("last_lon")
        if lat is None or lon is None:
            lat, lon = routing.random_road_point_in_zone(grid, home, rng)
        vehicle_raw = redis.get(redis_client.vehicle_key(driver_id))
        vehicle_type = "economy"
        if vehicle_raw:
            vehicle_type = json.loads(vehicle_raw).get("vehicle_type") or "economy"
        drivers[driver_id] = Driver(
            driver_id=driver_id, lat=float(lat), lon=float(lon), home_zone_id=home,
            vehicle_type=vehicle_type,
        )
    return drivers


def read_hotspots(redis, zone_ids: list[str]) -> dict[str, float]:
    """The current demand score of every servicable zone.

    One round trip for all zones instead of one per zone: at 36 zones the
    difference is small, but this runs every few seconds forever.
    """
    period = day_period(utc_now())
    keys = [redis_client.hotspot_key(zone_id, period) for zone_id in zone_ids]
    values = redis.mget(keys)

    scores: dict[str, float] = {}
    for zone_id, raw in zip(zone_ids, values):
        if not raw:
            continue
        try:
            scores[zone_id] = float(json.loads(raw)["demand_score"])
        except (ValueError, KeyError, TypeError):
            continue
    return scores


def apply_trip_news(consumer: AvroTopicConsumer, drivers: dict[str, Driver], redis) -> int:
    """Read whatever trip news is waiting, without blocking the tick.

    The service cannot sit and wait for messages: it has a fleet to move. So
    it drains what is there and carries on.
    """
    handled = 0
    while handled < 500:
        message = consumer.poll_once(timeout=0.0)
        if message is None:
            break
        handled += 1

        _, _, value = message
        if not value:
            continue

        driver_id = value.get("driver_id")
        driver = drivers.get(driver_id) if driver_id else None
        if driver is None:
            continue

        new_status = STATUS_EFFECT.get(value.get("status"))
        if new_status is None:
            continue

        if new_status == IDLE:
            driver.set_status(IDLE, None)
            driver.head_towards(driver.lat, driver.lon)
            continue

        trip_id = value.get("trip_id")
        driver.set_status(new_status, trip_id)

        # Where to head next comes from the live trip state dispatch wrote.
        active = redis.get(redis_client.trip_active_key(trip_id)) if trip_id else None
        if not active:
            continue
        trip = json.loads(active)
        if new_status == EN_ROUTE_PICKUP:
            driver.head_towards(float(trip["pickup_lat"]), float(trip["pickup_lon"]))
        else:
            driver.head_towards(float(trip["dropoff_lat"]), float(trip["dropoff_lon"]))

    return handled


def sync_to_database(drivers: dict[str, Driver]) -> int:
    """Write the last known state of every changed driver.

    Only the changed ones, and only on a timer. The stream of positions lives
    in Kafka; the database keeps the answer to "where was this driver last
    seen", which does not need updating twenty times a minute.
    """
    changed = [d for d in drivers.values() if d.dirty]
    if not changed:
        return 0

    rows = [
        {
            "driver_id": d.driver_id,
            "status": d.status,
            "lat": d.lat,
            "lon": d.lon,
        }
        for d in changed
    ]
    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE drivers
                   SET status = %(status)s,
                       last_lat = %(lat)s,
                       last_lon = %(lon)s,
                       last_seen_at = now(),
                       updated_at = now()
                 WHERE driver_id = %(driver_id)s
                """,
                rows,
            )
        conn.commit()

    for driver in changed:
        driver.dirty = False
    return len(changed)


def record_shift_changes(starts: list[tuple[str, str]], ends: list[str]) -> None:
    """Open a session for every driver going online, close one for every
    driver going offline - this tick's shift changes only."""
    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            if starts:
                # ON CONFLICT, not a bare INSERT: driver_sessions_one_open_idx
                # is a real constraint (see the migration), and a duplicate
                # here would be a bug worth investigating, not a reason to
                # crash the whole tick and stop reporting every other
                # driver's position too.
                cur.executemany(
                    "INSERT INTO driver_sessions (driver_id, started_at, zone_id) "
                    "VALUES (%s, now(), %s) "
                    "ON CONFLICT (driver_id) WHERE ended_at IS NULL DO NOTHING",
                    starts,
                )
            if ends:
                # The most recent open session for this driver - there
                # should only ever be one, since a driver can't go offline
                # twice without going online in between.
                cur.executemany(
                    "UPDATE driver_sessions SET ended_at = now() "
                    "WHERE session_id = ("
                    "    SELECT session_id FROM driver_sessions "
                    "     WHERE driver_id = %s AND ended_at IS NULL "
                    "     ORDER BY started_at DESC LIMIT 1"
                    ")",
                    [(driver_id,) for driver_id in ends],
                )
        conn.commit()


def main() -> int:
    setup_logging("driver-service")
    shutdown = Shutdown()
    rng = random.Random(config.integer("RANDOM_SEED", 20250824))
    grid = CityGrid.load()

    tick_seconds = config.number("DRIVER_TICK_SECONDS", 3.0)
    speed_kmh = config.number("DRIVER_SPEED_KMH", 25.0)
    online_share = config.number("DRIVER_ONLINE_SHARE", 0.6)
    shift_change_chance = config.number("DRIVER_SHIFT_CHANGE_CHANCE", 0.01)
    db_sync_seconds = config.number("DRIVER_DB_SYNC_SECONDS", 30.0)
    hotspot_refresh_seconds = config.number("DRIVER_HOTSPOT_REFRESH_SECONDS", 60.0)

    # Four connections, not one: this service owns driver:*/vehicle:*/the
    # geo sets (DB_DRIVER), but also reads hotspot scores to decide where an
    # idle driver drifts (DB_DEMAND) and live trip state to know where a
    # driver on a trip is actually heading (DB_TRIP) - real cross-domain
    # reads, not something a single db could avoid.
    redis_driver = redis_client.primary(redis_client.DB_DRIVER)
    redis_demand = redis_client.primary(redis_client.DB_DEMAND)
    redis_trip = redis_client.primary(redis_client.DB_TRIP)

    # Nothing to simulate until the drivers exist.
    wait_for_bootstrap(redis_client.primary(redis_client.DB_SYSTEM), shutdown)

    # The cache is filled by cache-updater from Debezium's first pass over the
    # seeded rows, which starts only once the CDC connector is registered — so
    # for the first minute or so after a fresh bootstrap there are no drivers
    # to load yet. Wait for them rather than exiting: this is a normal state
    # of a stack that has just come up, not a failure.
    wait_for(
        lambda: bool(next(redis_driver.scan_iter(match="driver:*", count=1), None)),
        description="cache-updater to fill the driver profiles (Redis driver:*)",
        attempts=120,
        delay_seconds=5.0,
        shutdown=shutdown,
    )
    # Computed once, here: a zone whose own centroid cannot reach a real
    # road (see routing.servicable_zone_ids()) is excluded from home-zone
    # assignment, hotspot scoring, and wander targets, for the whole run.
    zone_ids = routing.servicable_zone_ids()

    drivers = load_roster(redis_driver, grid, rng, zone_ids)
    if not drivers:
        log.error("driver keys appeared but none could be read")
        return 1
    log.info("fleet loaded", extra={"drivers": len(drivers)})

    # Start with the intended share of the fleet already working, so the
    # stack does not look empty for the first ten minutes.
    for driver in drivers.values():
        if rng.random() < online_share:
            driver.set_status(IDLE)
            driver.head_towards(*routing.random_road_point_in_zone(grid, driver.home_zone_id, rng))

    producer = AvroTopicProducer(TOPIC)
    consumer = AvroTopicConsumer(
        topics=[LIFECYCLE_TOPIC],
        group_id=config.optional("KAFKA_GROUP_ID", "driver-service"),
        # Only what happens from now on: old trip news is history, and this
        # service holds no state that needs rebuilding from it.
        from_beginning=False,
    )

    zone_scores = read_hotspots(redis_demand, zone_ids)
    last_hotspot_refresh = time.monotonic()
    last_db_sync = time.monotonic()
    sent = 0

    try:
        while not shutdown.requested:
            started = time.monotonic()

            apply_trip_news(consumer, drivers, redis_trip)

            if started - last_hotspot_refresh >= hotspot_refresh_seconds:
                zone_scores = read_hotspots(redis_demand, zone_ids)
                last_hotspot_refresh = started

            now = utc_now()
            event_time = to_millis(now)
            # Grouped by vehicle_type: dispatch searches one tier's GEO set
            # at a time (see redis_client.geo_available_drivers_key), so the
            # publish side has to group the same way.
            free_drivers: dict[str, list[tuple]] = {t: [] for t in redis_client.VEHICLE_TYPES}
            busy_drivers: dict[str, list[str]] = {t: [] for t in redis_client.VEHICLE_TYPES}
            session_starts: list[tuple[str, str]] = []
            session_ends: list[str] = []

            for driver in drivers.values():
                # Drivers start and end shifts. Without this the fleet would
                # be the same size at 4am as at 6pm.
                if rng.random() < shift_change_chance:
                    if driver.status == OFFLINE:
                        driver.set_status(IDLE)
                        driver.head_towards(*routing.random_road_point_in_zone(grid, driver.home_zone_id, rng))
                        session_starts.append((driver.driver_id, grid.zone_of(driver.lat, driver.lon)))
                    elif driver.status == IDLE:
                        driver.set_status(OFFLINE)
                        session_ends.append(driver.driver_id)

                if not driver.online:
                    busy_drivers[driver.vehicle_type].append(driver.driver_id)
                    continue

                # A free driver that has arrived picks a new place to drift
                # to, pulled towards whichever zone is busy right now.
                if driver.status == IDLE and driver.arrived():
                    target_zone = pick_target_zone(zone_scores, zone_ids, rng)
                    driver.head_towards(*routing.random_road_point_in_zone(grid, target_zone, rng))

                driver.move(tick_seconds, speed_kmh, rng)

                producer.send(
                    key=driver.driver_id,
                    value={
                        "driver_id": driver.driver_id,
                        "trip_id": driver.trip_id,
                        "status": driver.status,
                        "lat": driver.lat,
                        "lon": driver.lon,
                        "heading_deg": float(driver.heading_deg),
                        "speed_kmh": float(driver.speed_kmh),
                        "zone_id": grid.zone_of(driver.lat, driver.lon),
                        "event_time": event_time,
                    },
                )
                sent += 1

                if driver.free:
                    free_drivers[driver.vehicle_type].append((driver.lon, driver.lat, driver.driver_id))
                else:
                    busy_drivers[driver.vehicle_type].append(driver.driver_id)

            # The set dispatch searches, one per tier. Free drivers are added
            # with their position; everyone else is taken out, so a busy
            # driver can never be offered a second trip.
            with redis_driver.pipeline() as pipe:
                for vehicle_type in redis_client.VEHICLE_TYPES:
                    key = redis_client.geo_available_drivers_key(vehicle_type)
                    if free_drivers[vehicle_type]:
                        pipe.geoadd(key, [
                            item for driver in free_drivers[vehicle_type] for item in driver
                        ])
                    if busy_drivers[vehicle_type]:
                        pipe.zrem(key, *busy_drivers[vehicle_type])
                pipe.execute()

            if session_starts or session_ends:
                # Written as soon as it happens, not batched onto
                # db_sync_seconds's timer - a shift change is a discrete
                # event, worth its own row the moment it occurs, the same
                # way dispatch-service writes a trip status change at once
                # rather than on a delay.
                record_shift_changes(session_starts, session_ends)

            if started - last_db_sync >= db_sync_seconds:
                updated = sync_to_database(drivers)
                last_db_sync = started
                log.info(
                    "tick",
                    extra={
                        "online": sum(1 for d in drivers.values() if d.online),
                        "free": sum(len(v) for v in free_drivers.values()),
                        "positions_sent": sent,
                        "database_rows_updated": updated,
                    },
                )

            # Keep the tick length steady whatever the work took.
            elapsed = time.monotonic() - started
            if shutdown.wait(max(tick_seconds - elapsed, 0.0)):
                break

    finally:
        producer.flush()
        sync_to_database(drivers)
        consumer.close()
        log.info("stopped", extra={"positions_sent": sent})

    return 0


if __name__ == "__main__":
    sys.exit(main())
