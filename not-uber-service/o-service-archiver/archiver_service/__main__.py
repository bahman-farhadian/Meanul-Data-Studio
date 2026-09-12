"""Prunes trips out of PostgreSQL once they are safely archived elsewhere.

The OLTP/OLAP split this whole project is built on cuts both ways: once a
trip is over, the record of it belongs in the analytical warehouse, not
the live database. trip_events (e-infra-clickhouse) already gets that
continuously - clickhouse-sink consumes the same trip_lifecycle events
every trip publishes as it happens, so nothing here triggers or waits on
an archival step. This is the other half of the split that was missing:
without it, trips grows for as long as the stack runs live, no different
from why driver_positions/driver_location needed their own retention cut.

Retention is measured from ended_at, not requested_at: a trip is not
safe to prune until it is actually over. Only completed/cancelled_by_.../
no_driver_found trips ever set ended_at (l-service-dispatch's own
UPDATE), so ended_at IS NOT NULL already means "in a terminal state" -
no separate status check needed.

trip_events does not carry the trip's route geometry (route, a Postgres-
only geometry column) or a handful of other operational fields (the
pickup/dropoff points as geometry, requested_vehicle_type, attributes) -
deliberately: those matter while a trip is recent, not months later, and
carrying them into ClickHouse would just move the same storage cost
somewhere else rather than removing it. What is lost on prune is the
exact geometry and a few operational fields; what survives in trip_events
is everything the historical/analytical side (Superset, the daily
rollups) actually reads.
"""

import sys
import time

from nus_common import config, postgres, redis_client
from nus_common.lifecycle import Shutdown, wait_for_bootstrap
from nus_common.logging import get_logger, setup_logging

log = get_logger(__name__)

# LIMIT, not a single unbounded DELETE: a stack that ran a long time before
# this service ever existed could have a large backlog, and one giant
# transaction would hold a lock and generate a WAL burst neither
# replication nor a live simulation should have to absorb in one go.
PRUNE_BATCH_SQL = """
    SELECT trip_id FROM trips
     WHERE ended_at IS NOT NULL
       AND ended_at < now() - %(retention)s::interval
     LIMIT %(batch_size)s
"""

# trip_ratings.trip_id REFERENCES trips(trip_id) with no ON DELETE CASCADE
# - ratings for a pruned trip must go first, or the trips delete fails its
# own foreign key.
DELETE_RATINGS_SQL = "DELETE FROM trip_ratings WHERE trip_id = ANY(%(trip_ids)s)"
DELETE_TRIPS_SQL = "DELETE FROM trips WHERE trip_id = ANY(%(trip_ids)s)"


def _prune_batch(retention: str, batch_size: int) -> int:
    with postgres.write_connection() as conn:
        rows = postgres.fetch_all(conn, PRUNE_BATCH_SQL, {"retention": retention, "batch_size": batch_size})
        trip_ids = [row["trip_id"] for row in rows]
        if not trip_ids:
            return 0
        with conn.cursor() as cur:
            cur.execute(DELETE_RATINGS_SQL, {"trip_ids": trip_ids})
            cur.execute(DELETE_TRIPS_SQL, {"trip_ids": trip_ids})
        conn.commit()
    return len(trip_ids)


def main() -> int:
    setup_logging("archiver-service")
    shutdown = Shutdown()

    tick_seconds = config.number("ARCHIVER_TICK_MINUTES", 15.0) * 60
    retention = f"{config.integer('ARCHIVER_RETENTION_HOURS', 24)} hours"
    batch_size = config.integer("ARCHIVER_BATCH_SIZE", 5000)
    # A ceiling on one tick's own work, not on total pruning - a real
    # backlog just takes more ticks, rather than one tick running forever
    # and starving the timer that is supposed to pace it.
    max_batches_per_tick = config.integer("ARCHIVER_MAX_BATCHES_PER_TICK", 20)

    wait_for_bootstrap(redis_client.primary(redis_client.DB_SYSTEM), shutdown)

    pruned_total = 0
    try:
        while not shutdown.requested:
            started = time.monotonic()
            pruned_this_tick = 0
            for _ in range(max_batches_per_tick):
                n = _prune_batch(retention, batch_size)
                pruned_this_tick += n
                if n < batch_size:
                    break
            pruned_total += pruned_this_tick
            if pruned_this_tick:
                log.info(
                    "trips pruned",
                    extra={"this_tick": pruned_this_tick, "total": pruned_total, "retention": retention},
                )
            elapsed = time.monotonic() - started
            if shutdown.wait(max(tick_seconds - elapsed, 0.0)):
                break
    finally:
        log.info("stopped", extra={"pruned_total": pruned_total})

    return 0


if __name__ == "__main__":
    sys.exit(main())
