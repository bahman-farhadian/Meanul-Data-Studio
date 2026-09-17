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
# nus.foo after FROM/JOIN, not after a function name.
TABLE_REF = re.compile(
    r"(?i)(?:FROM|JOIN)\s+(?:nus\.)?([a-z][a-z0-9_]*)"
)

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
                if re.search(r"nus\.[a-z0-9_]+_local\b", sql):
                    errors.append(f"{uid} queries a *_local table: {sql[:80]!r}")
                for name in TABLE_REF.findall(sql):
                    seen_tables.add(name)
                    if name not in allowed:
                        errors.append(f"{uid} unknown table {name!r} (not a Distributed name in ddl/)")
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
