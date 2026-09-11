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

# Which cdc topic feeds which Redis key, and which of the numbered
# databases (nus_common.redis_client.DB_*) that key belongs to. This is the
# one service that writes across every domain - everyone else's own cache
# reads live in one db, but cache-updater is the thing that puts them
# there, straight from whichever table changed.
TOPIC_MAP = {
    "cdc.drivers": ("driver_id", redis_client.driver_key, redis_client.DB_DRIVER),
    "cdc.vehicles": ("driver_id", redis_client.vehicle_key, redis_client.DB_DRIVER),
    "cdc.passengers": ("passenger_id", redis_client.passenger_key, redis_client.DB_PASSENGER),
    "cdc.trips": ("trip_id", redis_client.trip_key, redis_client.DB_TRIP),
    "cdc.city_zones": ("zone_id", redis_client.zone_key, redis_client.DB_DEMAND),
}

# Debezium's word for what happened: created, updated, deleted, or read
# during the first pass over the existing rows.
WRITE_OPERATIONS = {"c", "u", "r"}
DELETE_OPERATION = "d"


def key_for(topic: str, key_value, row: dict | None) -> tuple[str, int] | None:
    """Work out which Redis key a message is about, and which db it lives in.

    The message key is the row's primary key, so it is the reliable source.
    The row itself is used as a fallback, because a tombstone has no row.
    """
    if topic not in TOPIC_MAP:
        return None
    id_column, build, db = TOPIC_MAP[topic]

    if isinstance(key_value, dict) and id_column in key_value:
        return build(str(key_value[id_column])), db
    if row and id_column in row:
        return build(str(row[id_column])), db
    return None


def main() -> int:
    setup_logging("cache-updater")
    shutdown = Shutdown()

    # A pattern, not a list: Debezium creates one topic per table, and a new
    # table should be picked up without editing this service.
    pattern = config.optional("CDC_TOPIC_PATTERN", "^cdc\\..*")
    batch_size = config.integer("CACHE_BATCH_SIZE", 500)
    flush_seconds = config.number("CACHE_FLUSH_SECONDS", 2.0)

    # One connection and one pipeline per db this service writes to - it is
    # the only one that spans every domain, so it is the only one that
    # needs more than one.
    dbs = sorted({db for _id_column, _build, db in TOPIC_MAP.values()})
    connections = {db: redis_client.primary(db) for db in dbs}
    pipelines = {db: connections[db].pipeline() for db in dbs}
    consumer = AvroTopicConsumer(
        topics=[pattern],
        group_id=config.optional("KAFKA_GROUP_ID", "cache-updater"),
        from_beginning=True,
        # Debezium encodes its keys as Avro, unlike the stack's own producers.
        avro_keys=True,
    )

    log.info("watching the change stream", extra={"pattern": pattern, "dbs": dbs})

    pending: dict[int, int] = {db: 0 for db in dbs}
    applied = 0
    skipped = 0
    last_flush = time.monotonic()

    def flush() -> None:
        nonlocal last_flush
        total = sum(pending.values())
        if total:
            # Every db's pipeline is sent before the Kafka offset moves, so
            # a crash between two dbs' flushes just repeats both on retry -
            # the same replay-safety the single-db version relied on.
            for db in dbs:
                if pending[db]:
                    pipelines[db].execute()
                    pending[db] = 0
            consumer.commit()
            log.info("applied to cache", extra={"changes": total, "total": applied})
        last_flush = time.monotonic()

    try:
        for topic, message_key, value in consumer.messages(lambda: shutdown.requested):
            # A message with no value is a tombstone: Debezium's marker that
            # the row is gone. It arrives right after the delete itself.
            if value is None:
                found = key_for(topic, message_key, None)
                if found:
                    target, db = found
                    pipelines[db].delete(target)
                    pending[db] += 1
                    applied += 1
                continue

            operation = value.get("op")
            row = value.get("after") or value.get("before")
            found = key_for(topic, message_key, row)

            if found is None:
                # A table nobody caches. Counted, not logged per message: at
                # snapshot time that would be thousands of identical lines.
                skipped += 1
                continue

            target, db = found
            if operation == DELETE_OPERATION:
                pipelines[db].delete(target)
            elif operation in WRITE_OPERATIONS and value.get("after"):
                # The whole row, as it now is. Writing the full state rather
                # than a change is what makes a replay harmless.
                pipelines[db].set(target, json.dumps(value["after"], default=str))
            else:
                skipped += 1
                continue

            pending[db] += 1
            applied += 1

            if sum(pending.values()) >= batch_size or (time.monotonic() - last_flush) >= flush_seconds:
                flush()

    finally:
        # Whatever is in hand belongs in Redis before the process ends.
        flush()
        consumer.close()
        log.info("stopped", extra={"applied": applied, "skipped": skipped})

    return 0


if __name__ == "__main__":
    sys.exit(main())
