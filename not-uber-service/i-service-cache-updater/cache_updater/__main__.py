"""Apply PostgreSQL changes to Redis.

This service closes the loop that makes the cache trustworthy. Debezium turns
every insert, update and delete in the database into a message on a cdc.*
topic; this service applies it to Redis. Nothing else has to remember to keep
the two in step.

Three properties matter, and each one is a decision in the code below:

**It is safe to replay.** Every change is written as "the row now looks like
this", not "add one to this". Applying the same message twice leaves exactly
the same result, so a restart that repeats a few messages is harmless.

**It does not wait for bootstrap.** Every other service waits for the marker
in Redis before doing anything. This one starts consuming at once, because it
is the thing that fills the cache the others will read. Waiting would be a
service waiting for itself.

**It commits after writing, not before.** Kafka is told how far we have got
only once Redis has accepted the batch. A crash in between repeats work,
which is harmless here; committing first would lose it, which is not.
"""

import json
import sys
import time

from nus_common import config, redis_client
from nus_common.kafka import AvroTopicConsumer
from nus_common.lifecycle import Shutdown
from nus_common.logging import get_logger, setup_logging

log = get_logger(__name__)

# Which cdc topic feeds which Redis key. The value is the column holding the
# row's id, and the function that builds the key name from it.
TOPIC_MAP = {
    "cdc.drivers": ("driver_id", redis_client.driver_key),
    "cdc.passengers": ("passenger_id", redis_client.passenger_key),
    "cdc.trips": ("trip_id", redis_client.trip_key),
    "cdc.city_zones": ("zone_id", redis_client.zone_key),
}

# Debezium's word for what happened: created, updated, deleted, or read
# during the first pass over the existing rows.
WRITE_OPERATIONS = {"c", "u", "r"}
DELETE_OPERATION = "d"


def key_for(topic: str, key_value, row: dict | None) -> str | None:
    """Work out which Redis key a message is about.

    The message key is the row's primary key, so it is the reliable source.
    The row itself is used as a fallback, because a tombstone has no row.
    """
    if topic not in TOPIC_MAP:
        return None
    id_column, build = TOPIC_MAP[topic]

    if isinstance(key_value, dict) and id_column in key_value:
        return build(str(key_value[id_column]))
    if row and id_column in row:
        return build(str(row[id_column]))
    return None


def main() -> int:
    setup_logging("cache-updater")
    shutdown = Shutdown()

    # A pattern, not a list: Debezium creates one topic per table, and a new
    # table should be picked up without editing this service.
    pattern = config.optional("CDC_TOPIC_PATTERN", "^cdc\\..*")
    batch_size = config.integer("CACHE_BATCH_SIZE", 500)
    flush_seconds = config.number("CACHE_FLUSH_SECONDS", 2.0)

    redis = redis_client.primary()
    consumer = AvroTopicConsumer(
        topics=[pattern],
        group_id=config.optional("KAFKA_GROUP_ID", "cache-updater"),
        from_beginning=True,
        # Debezium encodes its keys as Avro, unlike the stack's own producers.
        avro_keys=True,
    )

    log.info("watching the change stream", extra={"pattern": pattern})

    pipeline = redis.pipeline()
    pending = 0
    applied = 0
    skipped = 0
    last_flush = time.monotonic()

    def flush() -> None:
        nonlocal pending, last_flush
        if pending:
            pipeline.execute()
            consumer.commit()
            log.info("applied to cache", extra={"changes": pending, "total": applied})
            pending = 0
        last_flush = time.monotonic()

    try:
        for topic, message_key, value in consumer.messages(lambda: shutdown.requested):
            # A message with no value is a tombstone: Debezium's marker that
            # the row is gone. It arrives right after the delete itself.
            if value is None:
                target = key_for(topic, message_key, None)
                if target:
                    pipeline.delete(target)
                    pending += 1
                    applied += 1
                continue

            operation = value.get("op")
            row = value.get("after") or value.get("before")
            target = key_for(topic, message_key, row)

            if target is None:
                # A table nobody caches. Counted, not logged per message: at
                # snapshot time that would be thousands of identical lines.
                skipped += 1
                continue

            if operation == DELETE_OPERATION:
                pipeline.delete(target)
            elif operation in WRITE_OPERATIONS and value.get("after"):
                # The whole row, as it now is. Writing the full state rather
                # than a change is what makes a replay harmless.
                pipeline.set(target, json.dumps(value["after"], default=str))
            else:
                skipped += 1
                continue

            pending += 1
            applied += 1

            if pending >= batch_size or (time.monotonic() - last_flush) >= flush_seconds:
                flush()

    finally:
        # Whatever is in hand belongs in Redis before the process ends.
        flush()
        consumer.close()
        log.info("stopped", extra={"applied": applied, "skipped": skipped})

    return 0


if __name__ == "__main__":
    sys.exit(main())
