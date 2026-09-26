#!/usr/bin/env python3
"""Prove provisioned Grafana dashboards only query this stack's ClickHouse.

Loads every JSON under provisioning/dashboards/json/, asserts each panel
uses datasource uid nus-clickhouse, and that FROM/JOIN table names are
Distributed tables declared in e-infra-clickhouse/ddl/ (never Kafka,
Redis, Postgres, cdc.*, or *_local shard tables).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "provisioning" / "dashboards" / "json"
PROVIDER = HERE / "provisioning" / "dashboards" / "provider.yaml"
DDL_DIR = HERE.parent / "e-infra-clickhouse" / "ddl"
COMPOSE = HERE / "docker-compose.yaml"

DS_UID = "nus-clickhouse"
FORBIDDEN = re.compile(
    r"\b(kafka|redis|postgres|postgresql|cdc\.[a-z_]+|information_schema)\b",
    re.IGNORECASE,
)
# ClickHouse prefers SELECT aliases over table columns in WHERE (code 184
# when the alias is an aggregate). Never name an output column event_time
# or computed_at.
ALIAS_SHADOWS_COLUMN = re.compile(
    r"(?is)(?:max|min|argMax|argMin|anyLast|any)\s*\([^;]*?\)\s+AS\s+"
    r"(event_time|computed_at|hour)\b"
)
# nus.foo after FROM/JOIN, not after a function name.
TABLE_REF = re.compile(
    r"(?i)(?:FROM|JOIN)\s+(?:nus\.)?([a-z][a-z0-9_]*)"
)

# SummingMergeTree: a row is a PARTIAL sum until a merge that may not have
# happened yet. Averaging over rows therefore averages over an arbitrary
# grouping, and the answer changes as merges run. Every ratio taken from one
# of these has to sum first and divide once.
ROLLUPS = {
    "trip_stats_hourly",
    "trip_stats_daily",
    "od_matrix_daily",
    "fulfilment_hourly",
    "dispatch_funnel_hourly",
    "driver_utilization_hourly",
    "active_entities_hourly",
}
AGGREGATE = re.compile(
    r"(?i)\b(sum|count|countIf|sumIf|uniq|uniqExact|uniqMerge|quantile|"
    r"quantileMerge|min|max|avgWeighted)\s*\("
)
# An average of a stored rate, or of a partially merged sum. There is no
# correct use of it on these tables, so it is refused outright rather than
# argued about per panel.
AVERAGE = re.compile(r"(?i)\bavg(If|Merge|)\s*\(")

LIVE_REQUIRED = {"trip_events", "driver_positions"}
HISTORICAL_ANY = {
    "trip_stats_daily",
    "trip_duration_percentiles_hourly",
    "driver_utilization_hourly",
    "active_entities_hourly",
}
HISTORICAL_REQUIRED = {"trip_stats_hourly"}


def distributed_tables() -> set[str]:
    names: set[str] = set()
    for path in DDL_DIR.glob("*.sql"):
        text = path.read_text()
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS nus\.([a-z0-9_]+)", text
        ):
            name = match.group(1)
            if name.endswith("_local"):
                continue
            names.add(name)
    return names


def _check_live_ops_open_trips(dash: dict) -> list[str]:
    """trip_events rows are status changes, not heartbeats.

    A current in_progress trip whose last event is older than a short
    minute window must still appear. The lookback has to be hours, then
    argMax(status). The stat panel must show every GROUP BY status row
    (values=true), not collapse to lastNotNull of one series.
    """
    errors: list[str] = []
    by_title = {p.get("title"): p for p in dash.get("panels") or []}

    open_panel = by_title.get("Open trips by last status")
    if open_panel is None:
        return ["nus-live-ops missing panel 'Open trips by last status'"]
    sql = (open_panel.get("targets") or [{}])[0].get("rawSql") or ""
    if not re.search(r"INTERVAL\s+\d+\s+HOUR", sql, re.I):
        errors.append(
            "Open trips lookback must be INTERVAL n HOUR: "
            "trip_events are status changes, not heartbeats"
        )
    if re.search(r"INTERVAL\s+\d+\s+MINUTE", sql, re.I):
        errors.append("Open trips lookback must not use INTERVAL n MINUTE on trip_events")
    if "argMax(status" not in sql:
        errors.append("Open trips must take last status via argMax(status, event_time)")
    values = (
        (open_panel.get("options") or {})
        .get("reduceOptions", {})
        .get("values")
    )
    if values is not True:
        errors.append(
            "Open trips stat panel must set reduceOptions.values=true "
            "so each status is a stat, not lastNotNull of one count"
        )

    inprog = by_title.get("In-progress trips")
    if inprog is None:
        errors.append("nus-live-ops missing panel 'In-progress trips'")
    else:
        sql = (inprog.get("targets") or [{}])[0].get("rawSql") or ""
        if not re.search(r"INTERVAL\s+\d+\s+HOUR", sql, re.I):
            errors.append("In-progress trips lookback must be INTERVAL n HOUR")
        if re.search(r"INTERVAL\s+\d+\s+MINUTE", sql, re.I):
            errors.append("In-progress trips lookback must not use INTERVAL n MINUTE")
        if "HAVING" not in sql or "argMax(status" not in sql:
            errors.append("In-progress trips must HAVING argMax(status, event_time)")
    return errors


def _check_ratios(uid: str, pid, sql: str) -> list[str]:
    """Every division must have an aggregate on both sides.

    Catches the two ways a rate goes wrong on a SummingMergeTree: dividing
    two raw columns (each a partial sum), and averaging a ratio that was
    stored per row. Both look right and drift with merge history.
    """
    errors: list[str] = []
    if AVERAGE.search(sql):
        errors.append(
            f"{uid} panel {pid}: avg() over a rollup averages partial sums - "
            "use sum(x) / sum(y)"
        )
    for match in re.finditer(r"/", sql):
        before = sql[max(0, match.start() - 80):match.start()]
        after = sql[match.end():match.end() + 80]
        if not (AGGREGATE.search(before) and AGGREGATE.search(after)):
            errors.append(
                f"{uid} panel {pid}: division without an aggregate on both "
                f"sides: ...{sql[max(0, match.start() - 40):match.end() + 40].strip()}..."
            )
    return errors


def _chart_units(panel: dict) -> set[str]:
    """Every unit a charting panel puts on one set of axes."""
    config = panel.get("fieldConfig") or {}
    units = set()
    unit = (config.get("defaults") or {}).get("unit")
    if unit:
        units.add(unit)
    for override in config.get("overrides") or []:
        for prop in override.get("properties") or []:
            if prop.get("id") == "unit" and prop.get("value"):
                units.add(prop["value"])
    return units


CHARTS = {"timeseries", "barchart", "trend"}


def _overlaps(a: dict, b: dict) -> bool:
    return (
        a["x"] < b["x"] + b["w"]
        and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"]
        and b["y"] < a["y"] + a["h"]
    )


def _check_layout(uid: str, panels: list[dict]) -> list[str]:
    """No two panels may claim the same square of the grid.

    Grafana does not refuse overlapping gridPos - it reflows them, so a
    dashboard whose panels were renumbered by hand comes up looking fine on
    one screen width and scrambled on another.
    """
    errors: list[str] = []
    seen_ids: set = set()
    for panel in panels:
        pid = panel.get("id")
        if pid in seen_ids:
            errors.append(f"{uid}: two panels share id {pid}")
        seen_ids.add(pid)
    for i, first in enumerate(panels):
        for second in panels[i + 1:]:
            a, b = first.get("gridPos") or {}, second.get("gridPos") or {}
            if a and b and _overlaps(a, b):
                errors.append(
                    f"{uid}: panels {first.get('id')} and {second.get('id')} "
                    f"overlap at {a} / {b}"
                )
    return errors


def check() -> list[str]:
    errors: list[str] = []
    allowed = distributed_tables()
    if not allowed:
        errors.append(f"no Distributed tables found under {DDL_DIR}")
        return errors

    if not PROVIDER.is_file():
        errors.append(f"missing dashboards provider {PROVIDER}")
    else:
        text = PROVIDER.read_text()
        if "/etc/grafana/provisioning/dashboards" not in text:
            errors.append("provider.yaml does not point at the dashboards path")

    compose = COMPOSE.read_text()
    if "./provisioning:/etc/grafana/provisioning:ro" not in compose:
        errors.append("docker-compose.yaml does not bind-mount provisioning read-only")

    files = sorted(JSON_DIR.glob("*.json"))
    if not files:
        errors.append(f"no dashboard JSON in {JSON_DIR}")
        return errors

    seen_tables: set[str] = set()
    for path in files:
        dash = json.loads(path.read_text())
        uid = dash.get("uid") or path.stem
        for panel in dash.get("panels") or []:
            ds = panel.get("datasource") or {}
            if isinstance(ds, dict) and ds.get("uid") != DS_UID:
                errors.append(f"{uid} panel {panel.get('id')}: datasource uid {ds.get('uid')!r}")
            for target in panel.get("targets") or []:
                tds = target.get("datasource") or {}
                if isinstance(tds, dict) and tds.get("uid") != DS_UID:
                    errors.append(f"{uid} target {target.get('refId')}: datasource uid {tds.get('uid')!r}")
                sql = target.get("rawSql") or ""
                if FORBIDDEN.search(sql):
                    errors.append(f"{uid} forbids non-ClickHouse store in: {sql[:80]!r}")
                if ALIAS_SHADOWS_COLUMN.search(sql):
                    errors.append(
                        f"{uid} panel {panel.get('id')}: aggregate AS event_time/"
                        f"computed_at/hour shadows the column in WHERE (ClickHouse 184)"
                    )
                if "prefer_column_name_to_alias" in sql:
                    errors.append(
                        f"{uid} panel {panel.get('id')}: prefer_column_name_to_alias "
                        "makes ORDER BY alias match the raw column (ClickHouse 215)"
                    )
                if re.search(r"nus\.[a-z0-9_]+_local\b", sql):
                    errors.append(f"{uid} queries a *_local table: {sql[:80]!r}")
                tables = set(TABLE_REF.findall(sql))
                for name in tables:
                    seen_tables.add(name)
                    if name not in allowed:
                        errors.append(f"{uid} unknown table {name!r} (not a Distributed name in ddl/)")
                if tables & ROLLUPS or "/" in sql:
                    errors.extend(_check_ratios(uid, panel.get("id"), sql))

            # A chart with two units is two charts wearing one axis. The
            # smaller series is unreadable and a second y-axis only moves
            # the problem. Tables are exempt: a column carries its own unit.
            if panel.get("type") in CHARTS:
                units = _chart_units(panel)
                if not units:
                    errors.append(
                        f"{uid} panel {panel.get('id')} ({panel.get('title')!r}): "
                        "no unit - a share drawn as 0.42, a duration as 900, "
                        "or money as 12045 makes the reader do the conversion"
                    )
                if len(units) > 1:
                    errors.append(
                        f"{uid} panel {panel.get('id')}: {sorted(units)} on one "
                        "chart - split it, never a second axis"
                    )
        errors.extend(_check_layout(uid, dash.get("panels") or []))

        for var in (dash.get("templating") or {}).get("list") or []:
            q = var.get("query") or ""
            if isinstance(q, dict):
                q = q.get("rawSql") or ""
            q = str(q)
            if q:
                if FORBIDDEN.search(q):
                    errors.append(f"{uid} variable {var.get('name')} forbids non-ClickHouse store")
                for name in TABLE_REF.findall(q):
                    seen_tables.add(name)
                    if name not in allowed:
                        errors.append(f"{uid} variable unknown table {name!r}")

    missing_live = LIVE_REQUIRED - seen_tables
    if missing_live:
        errors.append(f"live tables missing from dashboards: {sorted(missing_live)}")
    missing_hourly = HISTORICAL_REQUIRED - seen_tables
    if missing_hourly:
        errors.append(f"historical table missing: {sorted(missing_hourly)}")
    if not (seen_tables & HISTORICAL_ANY):
        errors.append(
            "no historical rollup among "
            + ", ".join(sorted(HISTORICAL_ANY))
        )

    live_ops = JSON_DIR / "nus-live-ops.json"
    if live_ops.is_file():
        errors.extend(_check_live_ops_open_trips(json.loads(live_ops.read_text())))

    for path in JSON_DIR.glob("*.json"):
        blob = path.read_text()
        if '"type": "geomap"' in blob:
            if "/tiles/styles/nus/" not in blob or '"type": "xyz"' not in blob:
                errors.append(
                    f"{path.name}: geomap must use same-origin /tiles/styles/nus "
                    "(self-hosted OSM; no MapTiler key)"
                )
            if "maptiler" in blob.lower() or "cartocdn.com" in blob:
                errors.append(
                    f"{path.name}: geomap must not call a third-party tile CDN"
                )

    marketplace = JSON_DIR / "nus-marketplace.json"
    if not marketplace.is_file():
        errors.append("nus-marketplace.json is missing - the KPI tier is unprovisioned")
    else:
        dash = json.loads(marketplace.read_text())
        blob = marketplace.read_text()
        for needle in ("fulfilment_hourly", "dispatch_funnel_hourly"):
            if needle not in blob:
                errors.append(f"nus-marketplace does not read {needle}")
        for panel in dash.get("panels") or []:
            if not (panel.get("description") or "").strip():
                errors.append(
                    f"nus-marketplace panel {panel.get('id')} "
                    f"({panel.get('title')!r}) has no description - a KPI "
                    "nobody can define is a KPI nobody should act on"
                )

    history = JSON_DIR / "nus-history.json"
    if history.is_file():
        blob = history.read_text()
        for needle in ("sum(", "quantileMerge(", "uniqMerge("):
            if needle not in blob:
                errors.append(f"nus-history.json missing {needle}")

    print(f"dashboards: {len(files)}")
    print(f"distributed tables in ddl: {len(allowed)}")
    print(f"tables referenced: {', '.join(sorted(seen_tables))}")
    for path in files:
        dash = json.loads(path.read_text())
        print(f"  {dash.get('uid')}: {dash.get('title')} ({len(dash.get('panels') or [])} panels)")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("FAIL")
        for err in errors:
            print(f"  {err}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
