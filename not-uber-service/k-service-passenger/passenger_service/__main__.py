"""Run the simulated riders.

Every tick the service does three things:

  1. create some new ride requests, more of them at busy hours;
  2. read trip news, so it knows which of its riders are travelling;
  3. send a position report for each rider who is on a trip.

Why a trip request is written to PostgreSQL *and* sent to Kafka: the
database is the record that the request exists, and the message is how
dispatch-service hears about it. One is state, the other is news. Sending
only the message would mean a lost message is a lost trip; writing only the
row would mean dispatch has to poll the database.
"""

import json
import random
import sys
import time

from nus_common import config, postgres, redis_client
from nus_common.citygrid import CityGrid
from nus_common.geo import to_millis, utc_now
from nus_common.kafka import AvroTopicConsumer, AvroTopicProducer
from nus_common.lifecycle import Shutdown, wait_for, wait_for_bootstrap
from nus_common.logging import get_logger, setup_logging

from passenger_service.demand import requests_this_tick, zone_popularity

log = get_logger(__name__)

REQUEST_TOPIC = "trip_requests"
POSITION_TOPIC = "rider_location"
LIFECYCLE_TOPIC = "trip_lifecycle"

# Statuses that mean the rider is in the car and their phone should be
# reporting, and statuses that mean the trip is over.
TRAVELLING = {"in_progress"}
FINISHED = {
    "completed", "cancelled_by_passenger", "cancelled_by_driver", "no_driver_found"
}

INSERT_TRIP = """
    INSERT INTO trips (
        trip_id, rider_id, status,
        pickup_point, dropoff_point, pickup_zone_id, dropoff_zone_id,
        requested_at
    )
    VALUES (
        %(trip_id)s, %(rider_id)s, 'requested',
        ST_SetSRID(ST_MakePoint(%(pickup_lon)s, %(pickup_lat)s), 4326),
        ST_SetSRID(ST_MakePoint(%(dropoff_lon)s, %(dropoff_lat)s), 4326),
        %(pickup_zone_id)s, %(dropoff_zone_id)s,
        %(requested_at)s
    )
    ON CONFLICT (trip_id) DO NOTHING
"""


def load_rider_ids(redis) -> list[str]:
    """Every passenger id the cache knows about.

    Only the ids are kept. The service does not need names or preferences to
    simulate someone asking for a ride, and holding five thousand full
    profiles in memory would be five thousand things to keep fresh.
    """
    ids = []
    for key in redis.scan_iter(match="passenger:*", count=500):
        # The key is passenger:<id>, so the id can be taken from the name
        # itself - no need to fetch and parse the value.
        ids.append(key.split(":", 1)[1])
    return ids


def main() -> int:
    setup_logging("passenger-service")
    shutdown = Shutdown()
    rng = random.Random(config.integer("RANDOM_SEED", 20250824))
    grid = CityGrid.from_environment()

    tick_seconds = config.number("PASSENGER_TICK_SECONDS", 5.0)
    base_per_minute = config.number("TRIP_REQUESTS_PER_MINUTE", 40.0)

    redis = redis_client.primary()
    wait_for_bootstrap(redis, shutdown)

    # Same as driver-service: the profiles reach Redis through cache-updater
    # once Debezium has replayed the seeded rows, so an empty cache right
    # after a bootstrap means "not yet", not "broken".
    wait_for(
        lambda: bool(next(redis.scan_iter(match="passenger:*", count=1), None)),
        description="cache-updater to fill the passenger profiles (Redis passenger:*)",
        attempts=120,
        delay_seconds=5.0,
        shutdown=shutdown,
    )
    rider_ids = load_rider_ids(redis)
    if not rider_ids:
        log.error("passenger keys appeared but none could be read")
        return 1
    log.info("riders loaded", extra={"riders": len(rider_ids)})

    zone_ids = grid.all_zone_ids()
    zone_weights = [zone_popularity(zone_id) for zone_id in zone_ids]

    request_producer = AvroTopicProducer(REQUEST_TOPIC)
    position_producer = AvroTopicProducer(POSITION_TOPIC)
    consumer = AvroTopicConsumer(
        topics=[LIFECYCLE_TOPIC],
        group_id=config.optional("KAFKA_GROUP_ID", "passenger-service"),
        from_beginning=False,
    )

    # Trips this service is currently following, so it knows whose phone
    # should be reporting: trip_id -> rider_id.
    travelling: dict[str, str] = {}
    requested = 0
    positions = 0

    try:
        while not shutdown.requested:
            started = time.monotonic()
            now = utc_now()
            event_time = to_millis(now)

            # --- 1. new ride requests ----------------------------------
            count = requests_this_tick(base_per_minute, now.hour, tick_seconds, rng)
            new_trips = []
            for _ in range(count):
                rider_id = rng.choice(rider_ids)
                pickup_zone = rng.choices(zone_ids, weights=zone_weights, k=1)[0]
                dropoff_zone = rng.choices(zone_ids, weights=zone_weights, k=1)[0]
                pickup_lat, pickup_lon = grid.random_point_in(pickup_zone, rng)
                dropoff_lat, dropoff_lon = grid.random_point_in(dropoff_zone, rng)
                trip_id = f"trp-{now.strftime('%Y%m%d')}-{rng.getrandbits(32):08x}"

                new_trips.append(
                    {
                        "trip_id": trip_id,
                        "rider_id": rider_id,
                        "pickup_lat": pickup_lat, "pickup_lon": pickup_lon,
                        "dropoff_lat": dropoff_lat, "dropoff_lon": dropoff_lon,
                        "pickup_zone_id": pickup_zone,
                        "dropoff_zone_id": dropoff_zone,
                        "requested_at": now,
                    }
                )

            if new_trips:
                # The row first, then the message. If the process died in
                # between, dispatch would never hear about a trip that
                # exists - which shows up as a stuck 'requested' row and is
                # findable. The other order would announce a trip that was
                # never recorded, which is not.
                with postgres.write_connection() as conn:
                    with conn.cursor() as cur:
                        cur.executemany(INSERT_TRIP, new_trips)
                    conn.commit()

                for trip in new_trips:
                    request_producer.send(
                        key=trip["trip_id"],
                        value={
                            "trip_id": trip["trip_id"],
                            "rider_id": trip["rider_id"],
                            "pickup_lat": trip["pickup_lat"],
                            "pickup_lon": trip["pickup_lon"],
                            "dropoff_lat": trip["dropoff_lat"],
                            "dropoff_lon": trip["dropoff_lon"],
                            "pickup_zone_id": trip["pickup_zone_id"],
                            "passenger_count": rng.choices([1, 2, 3, 4], [70, 20, 7, 3])[0],
                            "requested_at": event_time,
                        },
                    )
                    requested += 1

            # --- 2. trip news ------------------------------------------
            handled = 0
            while handled < 500:
                message = consumer.poll_once(timeout=0.0)
                if message is None:
                    break
                handled += 1
                _, _, value = message
                if not value:
                    continue
                trip_id = value.get("trip_id")
                status = value.get("status")
                if status in TRAVELLING:
                    travelling[trip_id] = value.get("rider_id")
                elif status in FINISHED:
                    travelling.pop(trip_id, None)

            # --- 3. rider positions ------------------------------------
            for trip_id, rider_id in list(travelling.items()):
                raw = redis.get(redis_client.trip_active_key(trip_id))
                if not raw:
                    # Dispatch has cleared the trip; nothing more to report.
                    travelling.pop(trip_id, None)
                    continue
                trip = json.loads(raw)
                # The rider is in the car, so their phone is wherever the car
                # is. It reports less precisely than the driver's device.
                lat = float(trip.get("current_lat", trip["pickup_lat"]))
                lon = float(trip.get("current_lon", trip["pickup_lon"]))
                position_producer.send(
                    key=rider_id,
                    value={
                        "rider_id": rider_id,
                        "trip_id": trip_id,
                        "lat": lat,
                        "lon": lon,
                        "accuracy_m": float(rng.uniform(4.0, 40.0)),
                        "zone_id": grid.zone_of(lat, lon),
                        "event_time": event_time,
                    },
                )
                positions += 1

            if requested and requested % 100 < count:
                log.info(
                    "tick",
                    extra={
                        "requests_total": requested,
                        "travelling_now": len(travelling),
                        "positions_total": positions,
                    },
                )

            elapsed = time.monotonic() - started
            if shutdown.wait(max(tick_seconds - elapsed, 0.0)):
                break

    finally:
        request_producer.flush()
        position_producer.flush()
        consumer.close()
        log.info("stopped", extra={"requests": requested, "positions": positions})

    return 0


if __name__ == "__main__":
    sys.exit(main())
