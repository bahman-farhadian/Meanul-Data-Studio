"""Write every event into ClickHouse, with the parts Kafka does not carry.

The sink is the last step of the pipeline and the only writer the analytics
database has. It does three things:

**Collects.** Rows are gathered and inserted in batches. ClickHouse rewards
large inserts and punishes small ones, so a row at a time would turn a
healthy cluster into a merge queue.

**Enriches.** A trip event arrives knowing its own numbers but not the
context around it: how busy the pickup zone was, and therefore whether it
counts as a hotspot trip. That comes from Redis - never from the OLTP
database, which is the rule the whole stack is built on.

**Commits last.** The position in Kafka is saved only after ClickHouse has
accepted the batch. A crash in between repeats a batch, which shows up as a
few duplicated rows; committing first would lose it silently, which is far
worse in a table nobody re-reads.
"""

import json
import sys
import time

from nus_common import config, redis_client
from nus_common.geo import day_period, utc_now
from nus_common.kafka import AvroTopicConsumer
from nus_common.lifecycle import Shutdown, wait_for_bootstrap
from nus_common.logging import get_logger, setup_logging

from clickhouse_sink.batches import Batches

log = get_logger(__name__)

TOPICS = [
    "driver_location",
    "rider_location",
    "trip_requests",
    "trip_lifecycle",
    "city_hotspots",
]


class HotspotCache:
    """The demand score of each zone, refreshed on a timer.

    Looking a score up in Redis for every trip event would be one round trip
    per message. The scores only change every half minute anyway, so they are
    read in one go and reused until they are stale.
    """

    def __init__(self, redis, refresh_seconds: float) -> None:
        self._redis = redis
        self._refresh_seconds = refresh_seconds
        self._scores: dict[str, float] = {}
        self._loaded_at = 0.0

    def score(self, zone_id: str | None) -> float | None:
        if not zone_id:
            return None
        if time.monotonic() - self._loaded_at >= self._refresh_seconds:
            self._reload()
        return self._scores.get(zone_id)

    def _reload(self) -> None:
        period = day_period(utc_now())
        scores: dict[str, float] = {}
        for key in self._redis.scan_iter(match=f"hotspot:*:{period}", count=200):
            raw = self._redis.get(key)
            if not raw:
                continue
            try:
                zone_id = key.split(":")[1]
                scores[zone_id] = float(json.loads(raw)["demand_score"])
            except (IndexError, KeyError, ValueError, TypeError):
                continue
        self._scores = scores
        self._loaded_at = time.monotonic()


def main() -> int:
    setup_logging("clickhouse-sink")
    shutdown = Shutdown()

    batches = Batches(
        max_rows=config.integer("SINK_BATCH_ROWS", 5000),
        max_seconds=config.number("SINK_FLUSH_SECONDS", 5.0),
    )
    hotspot_threshold = config.number("HOTSPOT_SCORE_THRESHOLD", 0.6)

    redis = redis_client.primary()
    wait_for_bootstrap(redis, shutdown)

    hotspots = HotspotCache(redis, config.number("SINK_HOTSPOT_REFRESH_SECONDS", 30.0))
    consumer = AvroTopicConsumer(
        topics=TOPICS,
        group_id=config.optional("KAFKA_GROUP_ID", "clickhouse-sink"),
        # From the beginning: anything that reached Kafka belongs in
        # ClickHouse, including whatever arrived while the sink was down.
        from_beginning=True,
    )

    written = 0

    try:
        while not shutdown.requested:
            message = consumer.poll_once(timeout=1.0)
            if message is not None:
                topic, _, value = message
                if value:
                    _collect(batches, topic, value, hotspots, hotspot_threshold)

            if batches.due():
                written += batches.flush()
                # Only now is it safe to say these messages are dealt with.
                consumer.commit()

    finally:
        # Whatever is in hand belongs in ClickHouse before the process ends.
        written += batches.flush()
        consumer.commit()
        consumer.close()
        log.info("stopped", extra={"rows_written": written})

    return 0


def _collect(batches: Batches, topic: str, value: dict,
             hotspots: HotspotCache, threshold: float) -> None:
    """Turn one message into one row waiting to be inserted."""
    if topic == "driver_location":
        batches.add("nus.driver_positions", [
            value["driver_id"], value.get("trip_id"), value.get("status"),
            value["lat"], value["lon"],
            value.get("heading_deg"), value.get("speed_kmh"),
            value.get("zone_id") or "", _moment(value["event_time"]),
        ])
        return

    if topic == "rider_location":
        batches.add("nus.rider_positions", [
            value["rider_id"], value.get("trip_id"),
            value["lat"], value["lon"], value.get("accuracy_m"),
            value.get("zone_id") or "", _moment(value["event_time"]),
        ])
        return

    if topic == "city_hotspots":
        batches.add("nus.hotspot_history", [
            value["zone_id"], value["period"], value["demand_score"],
            value["open_requests"], value["available_drivers"],
            value["surge_multiplier"], _moment(value["computed_at"]),
        ])
        return

    if topic == "trip_lifecycle":
        predicted = value.get("predicted_duration_s")
        actual = value.get("actual_duration_s")
        # The number the whole "was the route right" question rests on.
        delta = None if (predicted is None or actual is None) else actual - predicted
        longer = None if delta is None else int(delta > 0)

        score = hotspots.score(value.get("pickup_zone_id"))
        is_hotspot = None if score is None else int(score >= threshold)

        batches.add("nus.trip_events", [
            value["trip_id"], value["rider_id"], value.get("driver_id"),
            value["status"], value.get("pickup_zone_id") or "",
            value.get("route_km"), predicted, actual, delta, longer,
            value.get("surge_multiplier"), score, is_hotspot,
            value.get("fare_estimate"), value.get("fare_final"),
            _moment(value["event_time"]),
        ])
        return

    # trip_requests is consumed but not stored on its own: every request also
    # appears on trip_lifecycle, either as a match or as no_driver_found, and
    # storing both would count the same trip twice.


def _moment(millis) -> object:
    """Turn Avro's millisecond timestamp into something ClickHouse accepts.

    The Avro decoder already hands back a datetime for a timestamp field, so
    usually there is nothing to do; the fallback is here for the case where a
    producer sent a plain number.
    """
    if isinstance(millis, (int, float)):
        from datetime import datetime, timezone

        return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
    return millis


if __name__ == "__main__":
    sys.exit(main())
