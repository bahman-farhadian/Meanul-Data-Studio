"""Loading zone-demand-prepare's real TLC calibration into Postgres.

The prepare step (h-bootstrap/zone-demand-prepare/) already did the real
work - streaming a month of TLC's own trip records and reducing it to two
small CSVs. This just gets them into zone_demand_calibration/
od_pair_calibration, which nus_common.demand_calibration reads from
everywhere else. Skipped, not an error, when the CSVs are not there:
SKIP_MAP_IMPORT-style local iteration should not require a multi-hundred-
MB download just to bring the stack up.
"""

import csv
from pathlib import Path

from nus_common import postgres
from nus_common.logging import get_logger

log = get_logger(__name__)

INSERT_ZONE_DEMAND = """
    INSERT INTO zone_demand_calibration (zone_id, hour_of_day, day_of_week, weight)
    VALUES (%(zone_id)s, %(hour_of_day)s, %(day_of_week)s, %(weight)s)
    ON CONFLICT (zone_id, hour_of_day, day_of_week) DO NOTHING
"""

INSERT_OD_PAIR = """
    INSERT INTO od_pair_calibration (pickup_zone_id, dropoff_zone_id, trip_share, avg_fare, avg_duration_s)
    VALUES (%(pickup_zone_id)s, %(dropoff_zone_id)s, %(trip_share)s, %(avg_fare)s, %(avg_duration_s)s)
    ON CONFLICT (pickup_zone_id, dropoff_zone_id) DO NOTHING
"""


def _load_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _zone_row(row: dict) -> dict:
    return {
        "zone_id": row["zone_id"],
        "hour_of_day": int(row["hour_of_day"]),
        "day_of_week": int(row["day_of_week"]),
        "weight": float(row["weight"]),
    }


def _od_row(row: dict) -> dict:
    # csv.DictReader hands back plain strings, never psycopg's own typed
    # values - avg_duration_s is integer in Postgres but a mean is not
    # naturally whole ("218.0"), which Postgres's own text-to-integer
    # parser rejects outright rather than truncating. round()+int() here
    # is the actual fix; prepare.py rounds it before writing the CSV too,
    # but a CSV already on disk from before that change still needs this.
    return {
        "pickup_zone_id": row["pickup_zone_id"],
        "dropoff_zone_id": row["dropoff_zone_id"],
        "trip_share": float(row["trip_share"]),
        "avg_fare": float(row["avg_fare"]) if row["avg_fare"] else None,
        "avg_duration_s": round(float(row["avg_duration_s"])) if row["avg_duration_s"] else None,
    }


def seed(data_dir: str) -> tuple[int, int]:
    """Load the calibration CSVs zone-demand-prepare built, if they exist.

    zone-demand-prepare already filtered out TLC's non-geographic
    placeholder ids (264 "Unknown", 265 "N/A") before writing the CSVs -
    every zone_id here is expected to already exist in city_zones. This is
    not a second safety net for that: ON CONFLICT only suppresses a
    primary-key/unique conflict, not a foreign-key violation, so a zone id
    city_zones genuinely does not have would still fail the whole batch
    loudly here, which is the right outcome if the upstream filter is ever
    wrong.
    """
    zone_path = Path(data_dir) / "zone_demand_calibration.csv"
    od_path = Path(data_dir) / "od_pair_calibration.csv"

    if not zone_path.exists() or not od_path.exists():
        log.warning(
            "no real demand calibration found - run make zone-demand-prepare "
            "(part of make prepare) first. Falling back to the floor weight "
            "for every zone/hour/day until then.",
            extra={"expected": str(zone_path)},
        )
        return 0, 0

    zone_rows = _load_csv(zone_path)
    od_rows = _load_csv(od_path)

    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(INSERT_ZONE_DEMAND, [_zone_row(row) for row in zone_rows])
            cur.executemany(INSERT_OD_PAIR, [_od_row(row) for row in od_rows])
        conn.commit()

    log.info("demand calibration loaded", extra={"zone_hours": len(zone_rows), "od_pairs": len(od_rows)})
    return len(zone_rows), len(od_rows)
