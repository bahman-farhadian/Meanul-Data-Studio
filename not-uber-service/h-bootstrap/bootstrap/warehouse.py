"""Loading the generated week into ClickHouse.

The point of this step is that the dashboards have something to show from
the first minute. Without it, Grafana and Superset would be empty until the
services had been running long enough to fill them.

Everything here is inserted in batches. ClickHouse is built for that: one
insert of ten thousand rows costs far less than ten thousand inserts of one.
"""

from nus_common import clickhouse
from nus_common.logging import get_logger

log = get_logger(__name__)

# Column orders, kept next to each other so a change in one is obvious in
# the others. They must match the DDL in e-infra-clickhouse/ddl/.
TRIP_EVENT_COLUMNS = [
    "trip_id", "rider_id", "driver_id", "status", "pickup_zone_id",
    "route_km", "predicted_duration_s", "actual_duration_s",
    "duration_delta_s", "took_longer_than_predicted",
    "surge_multiplier", "hotspot_score", "is_hotspot_trip",
    "fare_estimate", "fare_final", "event_time",
]

DRIVER_POSITION_COLUMNS = [
    "driver_id", "trip_id", "status", "lat", "lon",
    "heading_deg", "speed_kmh", "zone_id", "event_time",
]

RIDER_POSITION_COLUMNS = [
    "rider_id", "trip_id", "lat", "lon", "accuracy_m", "zone_id", "event_time",
]

HOTSPOT_COLUMNS = [
    "zone_id", "period", "demand_score", "open_requests",
    "available_drivers", "surge_multiplier", "computed_at",
]

BATCH_SIZE = 10000


def _load(table: str, rows: list[list], columns: list[str]) -> int:
    """Insert one table's rows, a batch at a time."""
    total = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        clickhouse.insert_rows(table, batch, columns)
        total += len(batch)
        log.info("loaded", extra={"table": table, "done": total, "of": len(rows)})
    return total


def already_loaded() -> bool:
    """True when the warehouse already holds trip events.

    Bootstrap may start a second time - a restarted container, a repeated
    run - and loading the same week twice would double every number on every
    dashboard. ClickHouse has no primary key to protect against that, so the
    check is done here.
    """
    count = clickhouse.client().command("SELECT count() FROM nus.trip_events")
    return int(count) > 0


def load_week(week) -> dict[str, int]:
    """Load everything the generator produced. Returns the counts."""
    return {
        "trip_events": _load("nus.trip_events", week.trip_events, TRIP_EVENT_COLUMNS),
        "driver_positions": _load(
            "nus.driver_positions", week.driver_positions, DRIVER_POSITION_COLUMNS
        ),
        "rider_positions": _load(
            "nus.rider_positions", week.rider_positions, RIDER_POSITION_COLUMNS
        ),
        "hotspot_history": _load(
            "nus.hotspot_history", week.hotspots, HOTSPOT_COLUMNS
        ),
    }
