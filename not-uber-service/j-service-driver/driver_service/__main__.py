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

from nus_common import config, postgres, redis_client
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


def load_roster(redis, grid: CityGrid, rng: random.Random) -> dict[str, Driver]:
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
        home = row.get("home_zone_id") or rng.choice(grid.all_zone_ids())
        lat = row.get("last_lat")
        lon = row.get("last_lon")
        if lat is None or lon is None:
            lat, lon = grid.random_point_in(home, rng)
        drivers[driver_id] = Driver(
            driver_id=driver_id, lat=float(lat), lon=float(lon), home_zone_id=home
        )
    return drivers


def read_hotspots(redis, grid: CityGrid) -> dict[str, float]:
    """The current demand score of every zone.

    One round trip for all zones instead of one per zone: at 36 zones the
    difference is small, but this runs every few seconds forever.
    """
    period = day_period(utc_now())
    zone_ids = grid.all_zone_ids()
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


def main() -> int:
    setup_logging("driver-service")
    shutdown = Shutdown()
    rng = random.Random(config.integer("RANDOM_SEED", 20250824))
    grid = CityGrid.from_environment()

    tick_seconds = config.number("DRIVER_TICK_SECONDS", 3.0)
    speed_kmh = config.number("DRIVER_SPEED_KMH", 25.0)
    online_share = config.number("DRIVER_ONLINE_SHARE", 0.6)
    shift_change_chance = config.number("DRIVER_SHIFT_CHANGE_CHANCE", 0.01)
    db_sync_seconds = config.number("DRIVER_DB_SYNC_SECONDS", 30.0)
    hotspot_refresh_seconds = config.number("DRIVER_HOTSPOT_REFRESH_SECONDS", 60.0)

    redis = redis_client.primary()

    # Nothing to simulate until the drivers exist.
    wait_for_bootstrap(redis, shutdown)

    # The cache is filled by cache-updater from Debezium's first pass over the
    # seeded rows, which starts only once the CDC connector is registered — so
    # for the first minute or so after a fresh bootstrap there are no drivers
    # to load yet. Wait for them rather than exiting: this is a normal state
    # of a stack that has just come up, not a failure.
    wait_for(
        lambda: bool(next(redis.scan_iter(match="driver:*", count=1), None)),
        description="cache-updater to fill the driver profiles (Redis driver:*)",
        attempts=120,
        delay_seconds=5.0,
        shutdown=shutdown,
    )
    drivers = load_roster(redis, grid, rng)
    if not drivers:
        log.error("driver keys appeared but none could be read")
        return 1
    log.info("fleet loaded", extra={"drivers": len(drivers)})

    # Start with the intended share of the fleet already working, so the
    # stack does not look empty for the first ten minutes.
    for driver in drivers.values():
        if rng.random() < online_share:
            driver.set_status(IDLE)
            driver.head_towards(*grid.random_point_in(driver.home_zone_id, rng))

    producer = AvroTopicProducer(TOPIC)
    consumer = AvroTopicConsumer(
        topics=[LIFECYCLE_TOPIC],
        group_id=config.optional("KAFKA_GROUP_ID", "driver-service"),
        # Only what happens from now on: old trip news is history, and this
        # service holds no state that needs rebuilding from it.
        from_beginning=False,
    )

    zone_ids = grid.all_zone_ids()
    zone_scores = read_hotspots(redis, grid)
    last_hotspot_refresh = time.monotonic()
    last_db_sync = time.monotonic()
    sent = 0

    try:
        while not shutdown.requested:
            started = time.monotonic()

            apply_trip_news(consumer, drivers, redis)

            if started - last_hotspot_refresh >= hotspot_refresh_seconds:
                zone_scores = read_hotspots(redis, grid)
                last_hotspot_refresh = started

            now = utc_now()
            event_time = to_millis(now)
            free_drivers: list[tuple] = []
            busy_drivers: list[str] = []

            for driver in drivers.values():
                # Drivers start and end shifts. Without this the fleet would
                # be the same size at 4am as at 6pm.
                if rng.random() < shift_change_chance:
                    if driver.status == OFFLINE:
                        driver.set_status(IDLE)
                        driver.head_towards(*grid.random_point_in(driver.home_zone_id, rng))
                    elif driver.status == IDLE:
                        driver.set_status(OFFLINE)

                if not driver.online:
                    busy_drivers.append(driver.driver_id)
                    continue

                # A free driver that has arrived picks a new place to drift
                # to, pulled towards whichever zone is busy right now.
                if driver.status == IDLE and driver.arrived():
                    target_zone = pick_target_zone(zone_scores, zone_ids, rng)
                    driver.head_towards(*grid.random_point_in(target_zone, rng))

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
                    free_drivers.append((driver.lon, driver.lat, driver.driver_id))
                else:
                    busy_drivers.append(driver.driver_id)

            # The list dispatch searches. Free drivers are added with their
            # position; everyone else is taken out, so a busy driver can
            # never be offered a second trip.
            with redis.pipeline() as pipe:
                if free_drivers:
                    pipe.geoadd(redis_client.GEO_AVAILABLE_DRIVERS, [
                        item for driver in free_drivers for item in driver
                    ])
                if busy_drivers:
                    pipe.zrem(redis_client.GEO_AVAILABLE_DRIVERS, *busy_drivers)
                pipe.execute()

            if started - last_db_sync >= db_sync_seconds:
                updated = sync_to_database(drivers)
                last_db_sync = started
                log.info(
                    "tick",
                    extra={
                        "online": sum(1 for d in drivers.values() if d.online),
                        "free": len(free_drivers),
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
