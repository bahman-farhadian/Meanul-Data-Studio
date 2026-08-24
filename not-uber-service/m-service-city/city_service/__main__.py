"""Watch the city, score demand, and keep the traffic factors current.

Two jobs, on two different clocks:

**Every half minute** it publishes a demand score for each zone. The score
goes to Redis, where drivers read it to know where to go and dispatch reads
it to price a trip, and to Kafka, where it becomes history in ClickHouse.

**Every few minutes** it updates the congestion factors in PostgreSQL, which
is what makes routing traffic-aware. That one is slower and touches the
database, so it runs far less often.

The service holds its picture of the city in memory only. After a restart it
rebuilds within a minute from the streams - much simpler than keeping a
durable copy of something that is only ever seconds old.
"""

import json
import sys
import time
from collections import defaultdict

from nus_common import config, postgres, redis_client
from nus_common.citygrid import CityGrid
from nus_common.geo import day_period, to_millis, utc_now
from nus_common.kafka import AvroTopicConsumer, AvroTopicProducer
from nus_common.lifecycle import Shutdown, wait_for_bootstrap
from nus_common.logging import get_logger, setup_logging

from city_service.counters import ZoneCounter, surge_from_score

log = get_logger(__name__)

HOTSPOT_TOPIC = "city_hotspots"
WATCHED_TOPICS = ["driver_location", "rider_location", "trip_lifecycle"]

# A trip in one of these statuses is a rider still waiting for a car.
WAITING = {"requested", "matched"}
# In any of these it is no longer waiting - it has a car, or it is over.
NOT_WAITING = {
    "accepted", "en_route_pickup", "in_progress", "completed",
    "cancelled_by_passenger", "cancelled_by_driver", "no_driver_found",
}

# Blend new congestion readings into the stored factor instead of replacing
# it. A factor that jumped with every reading would make routing unstable,
# and two identical trips a minute apart would get different answers.
SMOOTHING = 0.3

UPDATE_TRAFFIC = """
    UPDATE segment_traffic st
       SET congestion_factor = st.congestion_factor * (1 - %(smoothing)s)
                             + %(factor)s * %(smoothing)s,
           sample_count = st.sample_count + %(samples)s,
           updated_at = now()
      FROM ways w, city_zones z
     WHERE st.way_id = w.gid
       AND st.period = %(period)s
       AND z.zone_id = %(zone_id)s
       AND ST_Intersects(w.the_geom, z.boundary)
"""


def main() -> int:
    setup_logging("city-service")
    shutdown = Shutdown()
    grid = CityGrid.from_environment()

    score_seconds = config.number("CITY_SCORE_SECONDS", 30.0)
    traffic_minutes = config.number("CITY_TRAFFIC_UPDATE_MINUTES", 5.0)
    hotspot_ttl = config.integer("HOTSPOT_TTL_SECONDS", redis_client.HOTSPOT_TTL_SECONDS)
    hotspot_threshold = config.number("HOTSPOT_SCORE_THRESHOLD", 0.6)

    redis = redis_client.primary()
    wait_for_bootstrap(redis, shutdown)

    producer = AvroTopicProducer(HOTSPOT_TOPIC)
    consumer = AvroTopicConsumer(
        topics=WATCHED_TOPICS,
        group_id=config.optional("KAFKA_GROUP_ID", "city-service"),
        # Only what is happening now. Replaying yesterday's positions would
        # produce a picture of yesterday's city.
        from_beginning=False,
    )

    zones: dict[str, ZoneCounter] = defaultdict(ZoneCounter)
    # Which zone each waiting trip belongs to, so it can be removed from the
    # right counter when it stops waiting.
    waiting_zone: dict[str, str] = {}

    last_score = time.monotonic()
    last_traffic = time.monotonic()
    published = 0

    try:
        while not shutdown.requested:
            # Read for a moment, then do the periodic work. The timeout is
            # what stops this becoming a busy loop when the city is quiet.
            handled = 0
            while handled < 5000:
                message = consumer.poll_once(timeout=0.2 if handled == 0 else 0.0)
                if message is None:
                    break
                handled += 1
                topic, _, value = message
                if not value:
                    continue

                if topic == "driver_location":
                    _note_driver(zones, grid, value)
                elif topic == "trip_lifecycle":
                    _note_trip(zones, waiting_zone, value)
                # rider_location is watched but not counted: a rider's phone
                # says where a trip already under way is, which the driver's
                # device already told us more accurately.

            now_monotonic = time.monotonic()

            if now_monotonic - last_score >= score_seconds:
                published += _publish_scores(
                    zones, grid, redis, producer, hotspot_ttl, hotspot_threshold
                )
                consumer.commit()
                last_score = now_monotonic

            if now_monotonic - last_traffic >= traffic_minutes * 60:
                _update_traffic(zones)
                last_traffic = now_monotonic

    finally:
        producer.flush()
        consumer.close()
        log.info("stopped", extra={"scores_published": published})

    return 0


def _note_driver(zones: dict[str, ZoneCounter], grid: CityGrid, value: dict) -> None:
    """Update the supply side and the speed picture from one position report."""
    zone_id = value.get("zone_id") or grid.zone_of(value["lat"], value["lon"])
    counter = zones[zone_id]
    driver_id = value["driver_id"]
    status = value.get("status")

    if status == "idle":
        counter.free_drivers.add(driver_id)
    else:
        counter.free_drivers.discard(driver_id)

    if status == "on_trip":
        counter.note_speed(float(value.get("speed_kmh") or 0.0))


def _note_trip(
    zones: dict[str, ZoneCounter], waiting_zone: dict[str, str], value: dict
) -> None:
    """Update the demand side from one trip status change."""
    trip_id = value["trip_id"]
    zone_id = value.get("pickup_zone_id")
    status = value.get("status")

    if status in WAITING and zone_id:
        zones[zone_id].waiting_riders.add(trip_id)
        waiting_zone[trip_id] = zone_id
    elif status in NOT_WAITING:
        previous = waiting_zone.pop(trip_id, zone_id)
        if previous:
            zones[previous].waiting_riders.discard(trip_id)


def _publish_scores(
    zones: dict[str, ZoneCounter], grid: CityGrid, redis,
    producer: AvroTopicProducer, ttl_seconds: int, threshold: float,
) -> int:
    """Write the current demand score of every zone to Redis and to Kafka."""
    now = utc_now()
    period = day_period(now)
    event_time = to_millis(now)
    hot = 0

    with redis.pipeline() as pipe:
        for zone_id in grid.all_zone_ids():
            counter = zones[zone_id]
            score = counter.demand_score()
            surge = surge_from_score(score)
            waiting = len(counter.waiting_riders)
            free = len(counter.free_drivers)
            if score >= threshold:
                hot += 1

            payload = {
                "zone_id": zone_id,
                "period": period,
                "demand_score": score,
                "open_requests": waiting,
                "available_drivers": free,
                "surge_multiplier": surge,
            }

            # A lifetime on the key, so a stopped city-service leaves stale
            # scores behind for six hours at most instead of for ever.
            pipe.set(
                redis_client.hotspot_key(zone_id, period),
                json.dumps(payload),
                ex=ttl_seconds,
            )

            producer.send(key=zone_id, value={**payload, "computed_at": event_time})
        pipe.execute()

    log.info(
        "scores published",
        extra={"zones": len(grid.all_zone_ids()), "hot_zones": hot, "period": period},
    )
    return len(grid.all_zone_ids())


def _update_traffic(zones: dict[str, ZoneCounter]) -> None:
    """Push what cars are really doing back into the routing costs.

    This is the slowest thing the service does: it touches every road segment
    inside a zone, which is why it runs every few minutes rather than every
    tick. Zones with no speed reports are skipped, so a quiet zone keeps its
    existing factor instead of being handed a guess.
    """
    period = day_period(utc_now())
    updated = 0

    with postgres.write_connection() as conn:
        for zone_id, counter in zones.items():
            factor = counter.congestion_factor()
            if factor is None:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    UPDATE_TRAFFIC,
                    {
                        "zone_id": zone_id,
                        "period": period,
                        "factor": factor,
                        "samples": counter.speed_samples,
                        "smoothing": SMOOTHING,
                    },
                )
                updated += cur.rowcount
            counter.reset_speeds()
        conn.commit()

    log.info("traffic factors updated", extra={"segments": updated, "period": period})


if __name__ == "__main__":
    sys.exit(main())
