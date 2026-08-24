"""The bootstrap run, from start to finish.

Order matters, and every step is safe to run again:

  1. wait for PostgreSQL, Redis and ClickHouse to answer
  2. apply the SQL migrations that have not run yet
  3. import the street map, unless it is already imported
  4. create the city zones
  5. create the drivers and passengers
  6. invent a week of history and store it in PostgreSQL
  7. give the road segments a starting congestion factor
  8. load that week into ClickHouse, unless it is already there
  9. set system:bootstrap:done in Redis, which releases the services

Step 9 is the last line for a reason: while that key is missing, every
service waits. If bootstrap fails halfway, the services keep waiting instead
of generating trips for drivers that do not exist.

Nothing here writes to Kafka. The services do that once they start, and the
seeded rows reach Redis on their own - Debezium reads them out of the
database journal and cache-updater applies them.
"""

import sys
from pathlib import Path

from nus_common import clickhouse, postgres, redis_client
from nus_common.lifecycle import BOOTSTRAP_DONE_KEY, wait_for
from nus_common.logging import get_logger, setup_logging

from bootstrap import history, migrate, osm, people, settings as settings_module
from bootstrap import warehouse, zones

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

log = get_logger(__name__)


def main() -> int:
    setup_logging("bootstrap")
    settings = settings_module.load()
    log.info("bootstrap starting")

    # --- 1. wait for the infrastructure --------------------------------
    wait_for(postgres.ping, "PostgreSQL through lb-a", attempts=60, delay_seconds=5)
    redis = redis_client.primary()
    wait_for(lambda: redis.ping() is True, "Redis through Sentinel", attempts=60, delay_seconds=5)
    wait_for(clickhouse.ping, "ClickHouse through lb-a", attempts=60, delay_seconds=5)

    if redis.exists(BOOTSTRAP_DONE_KEY) and not settings.force_reseed:
        log.info(
            "the marker is already set, so this stack has been prepared before. "
            "Nothing to do. Set FORCE_RESEED=true to run the steps again."
        )
        return 0

    # --- 2. schema ------------------------------------------------------
    migrate.apply_all(MIGRATIONS_DIR)

    # --- 3. the street map ---------------------------------------------
    osm.import_map(settings)

    # --- 4 and 5. the city and the people -------------------------------
    zones.seed(settings)
    people.seed(settings)

    # --- 6 and 7. a week of history -------------------------------------
    week = history.generate(settings)
    history.store_trips(week.trip_rows)
    history.seed_segment_traffic()

    # --- 8. the same week in the warehouse ------------------------------
    if warehouse.already_loaded():
        log.info("ClickHouse already holds trip events, not loading the week again")
    else:
        counts = warehouse.load_week(week)
        log.info("warehouse loaded", extra=counts)

    # --- 9. release the services ----------------------------------------
    redis.set(BOOTSTRAP_DONE_KEY, "1")
    log.info("bootstrap finished - the services may start generating")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:  # noqa: BLE001 - the container must fail loudly
        get_logger(__name__).error("bootstrap failed", extra={"error": str(err)})
        raise
