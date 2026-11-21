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

from psycopg.errors import OperationalError, QueryCanceled

from hailpath_common import clickhouse, config, postgres, redis_client
from hailpath_common.citygrid import CityGrid
from hailpath_common.geo import day_period, to_millis, utc_now
from hailpath_common.kafka import AvroTopicConsumer, AvroTopicProducer
from hailpath_common.lifecycle import Shutdown, wait_for_bootstrap
from hailpath_common.logging import get_logger, setup_logging

from city_service.counters import ZoneCounter, surge_from_score
from city_service.drain import drain_budget_seconds, drain_queue_dry
from city_service.supply import (
    LAST_KNOWN_SPEEDS_SQL,
    apply_last_known_speeds,
    refresh_free_from_geo,
)

log = get_logger(__name__)

HOTSPOT_TOPIC = "city_hotspots"
TRAFFIC_TOPIC = "segment_traffic_updates"
# trip_lifecycle only. Free drivers and speeds are last-per-driver
# (Redis GEO and ClickHouse driver_last_known). driver_location is the
# GPS firehose; city must not ingest it to score demand.
WATCHED_TOPICS = ["trip_lifecycle"]

# A trip in one of these statuses is a rider still waiting for a car.
WAITING = {"requested", "matched"}
# In any of these it is no longer waiting - it has a car, or it is over.
NOT_WAITING = {
    "accepted", "en_route_pickup", "arrived", "in_progress", "completed",
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
    setup_logging("hailpath-city")
    shutdown = Shutdown()
    grid = CityGrid.load()

    score_seconds = config.number("CITY_SCORE_SECONDS", 30.0)
    traffic_minutes = config.number("CITY_TRAFFIC_UPDATE_MINUTES", 5.0)
    hotspot_ttl = config.integer("HOTSPOT_TTL_SECONDS", redis_client.HOTSPOT_TTL_SECONDS)
    hotspot_threshold = config.number("HOTSPOT_SCORE_THRESHOLD", 0.6)

    # Hotspot writes are DB_DEMAND. Free-driver GEO lives on DB_DRIVER.
    redis = redis_client.primary(redis_client.DB_DEMAND)
    redis_driver = redis_client.primary(redis_client.DB_DRIVER)
    wait_for_bootstrap(redis_client.primary(redis_client.DB_SYSTEM), shutdown)

    producer = AvroTopicProducer(HOTSPOT_TOPIC, "hailpath-city")
    traffic_producer = AvroTopicProducer(TRAFFIC_TOPIC, "hailpath-city")
    consumer = AvroTopicConsumer(
        topics=WATCHED_TOPICS,
        group_id=config.optional("KAFKA_GROUP_ID", "hailpath-city"),
        # Only what is happening now. Replaying yesterday's positions would
        # produce a picture of yesterday's city.
        from_beginning=False,
    )

    zones: dict[int, ZoneCounter] = defaultdict(ZoneCounter)
    # Which zone each waiting trip belongs to, so it can be removed from the
    # right counter when it stops waiting.
    waiting_zone: dict[int, int] = {}
    last_kafka_score: dict[int, float] = {}
    last_kafka_at = 0.0

    last_score = time.monotonic()
    last_traffic = time.monotonic()
    published = 0

    try:
        while not shutdown.requested:
            try:
                # Drain until empty or the budget runs out, then do the
                # periodic work. The timeout on the first poll stops this
                # becoming a busy loop when the city is quiet.
                handled = 0
                deadline = time.monotonic() + drain_budget_seconds(score_seconds)
                empty_since = None
                while True:
                    now = time.monotonic()
                    if now >= deadline:
                        break
                    message = consumer.poll_once(timeout=min(0.2, deadline - now))
                    if message is None:
                        if empty_since is None:
                            empty_since = now
                        elif drain_queue_dry(empty_since, now):
                            break
                        continue
                    empty_since = None
                    handled += 1
                    topic, _, value = message
                    if not value:
                        continue
                    if topic == "trip_lifecycle":
                        _note_trip(zones, waiting_zone, value)

                now_monotonic = time.monotonic()

                # Each half owns its own clock, and the clock is set BEFORE
                # the work rather than after. An exception between the two
                # used to skip the traffic update entirely and leave
                # last_score unset, so scores republished on every tick
                # while segment_traffic_updates stayed empty for the whole
                # life of the stack - two silent failures from one raised
                # commit. Whatever goes wrong in one half now costs that
                # half one interval, not the other half forever.
                if now_monotonic - last_score >= score_seconds:
                    last_score = now_monotonic
                    refresh_free_from_geo(zones, grid, redis_driver)
                    kafka_due = now_monotonic - last_kafka_at >= 300.0
                    published += _publish_scores(
                        zones, grid, redis, producer, hotspot_ttl, hotspot_threshold,
                        last_kafka_score, kafka_due,
                    )
                    if kafka_due:
                        last_kafka_at = now_monotonic
                    consumer.commit()

                if now_monotonic - last_traffic >= traffic_minutes * 60:
                    last_traffic = now_monotonic
                    try:
                        rows = clickhouse.client().query(LAST_KNOWN_SPEEDS_SQL).result_rows
                        apply_last_known_speeds(zones, rows)
                    except Exception:
                        log.exception("last-known speeds unread; traffic uses existing samples")
                    _update_traffic(zones, traffic_producer)
            except Exception:
                # Confirmed live: a single QueryCanceled on UPDATE_TRAFFIC
                # (statement_timeout=30s vs ST_Intersects over 172k ways)
                # killed the process. Docker restarted it five times in
                # three hours. The picture rebuilds from the streams; a
                # dead container does not.
                log.exception("tick failed; continuing")
                if shutdown.wait(1.0):
                    break

    finally:
        producer.flush()
        traffic_producer.flush()
        consumer.close()
        log.info("stopped", extra={"scores_published": published})

    return 0


def _note_trip(
    zones: dict[int, ZoneCounter], waiting_zone: dict[int, int], value: dict
) -> None:
    """Update the demand side from one trip status change."""
    trip_id = int(value["trip_id"])
    raw_zone = value.get("pickup_zone_id")
    zone_id = int(raw_zone) if raw_zone is not None else None
    status = value.get("status")

    if status in WAITING and zone_id:
        zones[zone_id].waiting_riders.add(trip_id)
        waiting_zone[trip_id] = zone_id
    elif status in NOT_WAITING:
        previous = waiting_zone.pop(trip_id, zone_id)
        if previous:
            zones[previous].waiting_riders.discard(trip_id)


def _publish_scores(
    zones: dict[int, ZoneCounter], grid: CityGrid, redis,
    producer: AvroTopicProducer, ttl_seconds: int, threshold: float,
    last_kafka_score: dict[int, float], kafka_due: bool,
) -> int:
    """Redis every tick. Kafka/CH when the score changes, or every 5 minutes."""
    now = utc_now()
    period = day_period(now)
    event_time = to_millis(now)
    hot = 0
    kafka_rows = 0

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

            previous = last_kafka_score.get(zone_id)
            if kafka_due or previous is None or previous != score:
                producer.send(key=zone_id, value={**payload, "computed_at": event_time})
                last_kafka_score[zone_id] = score
                kafka_rows += 1
        pipe.execute()

    log.info(
        "scores published",
        extra={
            "zones": len(grid.all_zone_ids()),
            "hot_zones": hot,
            "period": period,
            "kafka_rows": kafka_rows,
        },
    )
    return kafka_rows


def _update_traffic(zones: dict[int, ZoneCounter], traffic_producer: AvroTopicProducer) -> None:
    """Push what cars are really doing back into the routing costs.

    This is the slowest thing the service does: it touches every road segment
    inside a zone, which is why it runs every few minutes rather than every
    tick. Zones with no speed reports are skipped, so a quiet zone keeps its
    existing factor instead of being handed a guess.

    segment_traffic (Postgres) only ever holds the current factor - this is
    the one place that also announces the update on Kafka, which is what
    gives clickhouse-sink something to build a real history from. One event
    per zone/period, not per road segment: that is the real granularity this
    update already works at (one UPDATE_TRAFFIC statement touches every
    segment intersecting the zone with the same factor), so the event
    matches what actually happened rather than inventing a finer grain the
    source data does not have.

    The event carries the raw reading (counter.congestion_factor()), not
    the smoothed value UPDATE_TRAFFIC actually stores - deliberately: the
    smoothed result can differ per segment (each one's own prior factor
    smooths differently even against the same new reading), so there is no
    single "the" smoothed value for a zone to publish. The raw reading is
    the real, comparable signal - what traffic actually looked like in
    this zone at this time - and is already the number every downstream
    consumer of this event would want to chart.
    """
    period = day_period(utc_now())
    updated = 0
    now = utc_now()

    with postgres.write_connection() as conn:
        # Pool default is 30s (routing runaway ceiling). One zone's
        # ST_Intersects against 172k ways can exceed that once every
        # zone has speed samples. Commit per zone so a timeout does not
        # roll back the zones that already succeeded.
        for zone_id, counter in list(zones.items()):
            factor = counter.congestion_factor()
            if factor is None:
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = '120s'")
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
                    segments_updated = cur.rowcount
                    updated += segments_updated
                conn.commit()
            except (QueryCanceled, OperationalError) as err:
                conn.rollback()
                log.warning(
                    "traffic update skipped",
                    extra={"zone_id": zone_id, "error": str(err)},
                )
                counter.reset_speeds()
                continue
            traffic_producer.send(
                key=zone_id,
                value={
                    "zone_id": zone_id,
                    "period": period,
                    "congestion_factor": factor,
                    "speed_samples": counter.speed_samples,
                    "segments_updated": segments_updated,
                    "computed_at": to_millis(now),
                },
            )
            counter.reset_speeds()

    log.info("traffic factors updated", extra={"segments": updated, "period": period})


if __name__ == "__main__":
    sys.exit(main())
