"""Collecting rows until there are enough of them to be worth sending.

ClickHouse is built for large inserts and punished by small ones: every
insert creates a part on disk that later has to be merged away. Inserting a
row at a time would turn a healthy cluster into a merge queue.

So rows are collected here and sent in batches - when the batch is big
enough, or when it has waited long enough, whichever comes first. The time
limit matters as much as the size: at three in the morning a batch might
take minutes to fill, and a dashboard should not be minutes behind.
"""

import time
from dataclasses import dataclass, field

from nus_common import clickhouse
from nus_common.logging import get_logger

log = get_logger(__name__)

# The column order of each table, which must match the DDL in
# e-infra-clickhouse/ddl/. Kept together so a change in one is obvious
# against the others.
COLUMNS = {
    "nus.trip_events": [
        "trip_id", "rider_id", "driver_id", "status", "pickup_zone_id",
        "route_km", "predicted_duration_s", "actual_duration_s",
        "duration_delta_s", "took_longer_than_predicted",
        "surge_multiplier", "hotspot_score", "is_hotspot_trip",
        "fare_estimate", "fare_final", "event_time",
    ],
    "nus.driver_positions": [
        "driver_id", "trip_id", "status", "lat", "lon",
        "heading_deg", "speed_kmh", "zone_id", "event_time",
    ],
    "nus.rider_positions": [
        "rider_id", "trip_id", "lat", "lon", "accuracy_m", "zone_id", "event_time",
    ],
    "nus.hotspot_history": [
        "zone_id", "period", "demand_score", "open_requests",
        "available_drivers", "surge_multiplier", "computed_at",
    ],
}


@dataclass
class Batches:
    """One pile of waiting rows per table."""

    max_rows: int = 5000
    max_seconds: float = 5.0
    rows: dict[str, list[list]] = field(default_factory=lambda: {t: [] for t in COLUMNS})
    last_flush: float = field(default_factory=time.monotonic)

    def add(self, table: str, row: list) -> None:
        self.rows[table].append(row)

    @property
    def total(self) -> int:
        return sum(len(rows) for rows in self.rows.values())

    def due(self) -> bool:
        """True when the rows should go now."""
        if self.total >= self.max_rows:
            return True
        return self.total > 0 and (time.monotonic() - self.last_flush) >= self.max_seconds

    def flush(self) -> int:
        """Send every waiting row and return how many were sent."""
        sent = 0
        for table, rows in self.rows.items():
            if not rows:
                continue
            clickhouse.insert_rows(table, rows, COLUMNS[table])
            sent += len(rows)
            rows.clear()

        self.last_flush = time.monotonic()
        if sent:
            log.info("written to clickhouse", extra={"rows": sent})
        return sent
