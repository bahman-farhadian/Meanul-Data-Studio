#!/usr/bin/env python3
"""Prove the Superset bundle is importable, complete, and reads only rollups.

The Grafana tier has check-dashboards.py; this is its counterpart, and it
holds the same three lines:

  * only this stack's ClickHouse warehouse, never the read-replica
    PostgreSQL connection (SQL Lab exploration only, per its own docstring)
    and never a raw event or position table
  * every rate is sum(x) / sum(y), because the sources are SummingMergeTree
    and a row is a partial sum until a merge that may not have run
  * the bundle is internally closed - every uuid a chart or dashboard
    references is one this bundle actually defines

That last one is the failure a hand-maintained bundle actually has. A
dashboard whose position block names a chart that is not in charts/ imports
with a hole in it and nothing complains; a chart pointing at a dataset uuid
that does not exist is skipped silently by Superset's own importer, which
only imports charts whose dataset it found.

Run with no arguments, from anywhere.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
DDL_DIR = HERE.parent / "e-infra-clickhouse" / "ddl"

# Tables a chart may read. Rollups and one-row-per-trip facts only: a chart
# over a position table would scan the heaviest data in the warehouse every
# time somebody opens the page.
ALLOWED_TABLES = {
    "trip_stats_hourly",
    "trip_stats_daily",
    "od_matrix_daily",
    "fulfilment_hourly",
    "dispatch_funnel_hourly",
    "driver_utilization_hourly",
    "active_entities_hourly",
    "trip_duration_percentiles_hourly",
    "trip_facts",
}
FORBIDDEN_URI = re.compile(r"(?i)postgres|redis|kafka")
AGGREGATE = re.compile(
    r"(?i)\b(sum|count|countIf|uniq|uniqExact|uniqMerge|quantile|quantileMerge|"
    r"min|max|greatest|least|toFloat64)\s*\("
)
AVERAGE = re.compile(r"(?i)\bavg\s*\(")


def load(path: Path) -> dict:
    """Read one of our own YAML files.

    Deliberately not PyYAML: these documents are written by
    render_assets.py, every scalar is quoted or a JSON blob, and a parser
    that only understands that shape is a parser that cannot quietly accept
    a file the writer could not have produced.
    """
    document: dict = {}
    key = None
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "-")):
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if raw in {"", "null"}:
            document[key] = None
        elif raw in {"true", "false"}:
            document[key] = raw == "true"
        elif raw.startswith("'"):
            document[key] = raw[1:-1].replace("''", "'")
        elif raw.startswith(("{", "[")):
            document[key] = json.loads(raw)
        else:
            try:
                document[key] = int(raw)
            except ValueError:
                document[key] = raw
    return document


def distributed_tables() -> set[str]:
    names: set[str] = set()
    for path in DDL_DIR.glob("*.sql"):
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS nus\.([a-z0-9_]+)", path.read_text()
        ):
            if not match.group(1).endswith("_local"):
                names.add(match.group(1))
    return names


def columns_of(path: Path) -> set[str]:
    return {
        line.split(":", 1)[1].strip().strip("'")
        for line in path.read_text().splitlines()
        if line.strip().startswith("- column_name:")
    }


def metrics_of(path: Path) -> list[tuple[str, str]]:
    """(metric_name, expression) pairs, read from the nested list."""
    found = []
    name = None
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- metric_name:"):
            name = stripped.split(":", 1)[1].strip().strip("'")
        elif stripped.startswith("expression:") and name:
            expression = stripped.split(":", 1)[1].strip()
            found.append((name, expression[1:-1].replace("''", "'")))
            name = None
    return found


def check() -> list[str]:
    errors: list[str] = []

    if not ASSETS.is_dir():
        return [f"no assets directory at {ASSETS} - run render_assets.py"]

    metadata = ASSETS / "metadata.yaml"
    if not metadata.is_file():
        errors.append("metadata.yaml is missing; import-directory needs it")

    databases = sorted((ASSETS / "databases").glob("*.yaml"))
    datasets = sorted((ASSETS / "datasets").rglob("*.yaml"))
    charts = sorted((ASSETS / "charts").glob("*.yaml"))
    dashboards = sorted((ASSETS / "dashboards").glob("*.yaml"))

    if not (databases and datasets and charts and dashboards):
        errors.append(
            "the bundle is not complete: "
            f"{len(databases)} databases, {len(datasets)} datasets, "
            f"{len(charts)} charts, {len(dashboards)} dashboards - "
            "an empty BI tier passes every other check here"
        )
        return errors

    # ---- the one connection charts may use
    database_uuids: set[str] = set()
    for path in databases:
        config = load(path)
        database_uuids.add(config["uuid"])
        uri = config.get("sqlalchemy_uri") or ""
        if FORBIDDEN_URI.search(uri):
            errors.append(
                f"{path.name}: charts may only read the warehouse, not {uri!r}"
            )
        if "@" in uri.split("//", 1)[-1].split("/")[0].replace("nus@", ""):
            errors.append(f"{path.name}: the committed uri appears to carry a password")
        if config.get("allow_dml") is not False:
            errors.append(f"{path.name}: allow_dml must be false - charts only read")

    # ---- datasets point at real Distributed tables, and only allowed ones
    allowed_in_ddl = distributed_tables()
    dataset_uuids: dict[str, str] = {}
    for path in datasets:
        config = load(path)
        name = config["table_name"]
        dataset_uuids[config["uuid"]] = name
        if config["database_uuid"] not in database_uuids:
            errors.append(f"{path.name}: database_uuid is not in this bundle")
        if config.get("schema") != "nus":
            errors.append(f"{path.name}: schema must be nus")
        if name not in allowed_in_ddl:
            errors.append(f"{path.name}: {name!r} is not a Distributed table in ddl/")
        if name not in ALLOWED_TABLES:
            errors.append(
                f"{path.name}: {name!r} is not a rollup - dashboards read "
                "rollups, never raw events or positions"
            )
        if config.get("sql"):
            errors.append(
                f"{path.name}: virtual datasets carry SQL that Superset "
                "transpiles on import; keep these physical"
            )
        found = metrics_of(path)
        if not found:
            errors.append(f"{path.name}: no metrics - a chart would be free to avg()")
        columns = columns_of(path)
        for metric_name, expression in found:
            # ClickHouse prefers a SELECT alias over a table column, so
            # `sum(revenue) AS revenue` makes the NEXT metric's sum(revenue)
            # a sum of an aggregate: ILLEGAL_AGGREGATION, code 184, and only
            # when two such metrics are selected together - which is exactly
            # what a dashboard does. Three of these six datasets would not
            # query at all before this rule existed.
            if metric_name in columns:
                errors.append(
                    f"{path.name}: metric {metric_name!r} is named after a "
                    "column it reads; ClickHouse resolves the alias first and "
                    "the next metric becomes an aggregate inside an aggregate"
                )
            if AVERAGE.search(expression):
                errors.append(
                    f"{path.name}: metric {metric_name!r} uses avg() over a "
                    "partial sum - use sum(x) / sum(y)"
                )
            for match in re.finditer(r"/", expression):
                before = expression[:match.start()]
                after = expression[match.end():]
                if not (AGGREGATE.search(before) and AGGREGATE.search(after)):
                    errors.append(
                        f"{path.name}: metric {metric_name!r} divides without "
                        f"an aggregate on both sides: {expression!r}"
                    )

    # ---- every chart attaches to a dataset in this bundle
    chart_uuids: dict[str, str] = {}
    for path in charts:
        config = load(path)
        chart_uuids[config["uuid"]] = config["slice_name"]
        if config["dataset_uuid"] not in dataset_uuids:
            errors.append(
                f"{path.name}: dataset_uuid is not in this bundle - Superset "
                "skips such a chart silently"
            )
        if not (config.get("description") or "").strip():
            errors.append(
                f"{path.name}: no description - a KPI nobody can define is a "
                "KPI nobody should act on"
            )
        params = config.get("params") or {}
        if not params.get("viz_type"):
            errors.append(f"{path.name}: params carry no viz_type")

        # A table with a row limit and no stated sort keeps whatever the
        # database happened to return first. "Where demand goes unserved"
        # ordered by requests ascending and listed the QUIETEST zones -
        # the opposite of its title, with no error anywhere. The plugin's
        # precedence is series_limit_metric, then legacy_order_by, then
        # metrics[0], so all the spellings present have to agree.
        if params.get("viz_type") == "table":
            metrics = params.get("metrics") or []
            stated = {
                params.get(key)
                for key in ("series_limit_metric", "legacy_order_by",
                            "timeseries_limit_metric")
                if params.get(key)
            }
            if not stated:
                errors.append(
                    f"{path.name}: a table with row_limit "
                    f"{params.get('row_limit')} states no sort metric; the "
                    "limit would keep an arbitrary slice"
                )
            elif len(stated) > 1:
                errors.append(
                    f"{path.name}: sort metric spelled two ways: {sorted(stated)}"
                )
            elif metrics and stated != {metrics[0]}:
                errors.append(
                    f"{path.name}: sorts by {stated.pop()!r} but metrics[0] is "
                    f"{metrics[0]!r} - a version that falls back to metrics[0] "
                    "would order by a different column"
                )
            for name in stated:
                if name not in metrics:
                    errors.append(
                        f"{path.name}: sorts by {name!r}, which is not one of "
                        "its metrics"
                    )

        # A time range that names no column is silently not applied, so the
        # chart scans the table's whole retention while claiming a window.
        if params.get("time_range") and params["time_range"] != "No filter":
            if not params.get("granularity_sqla") and not params.get("x_axis"):
                errors.append(
                    f"{path.name}: time_range {params['time_range']!r} with no "
                    "granularity_sqla or x_axis - the window is not applied"
                )

    # ---- every dashboard references charts that exist, and uses them all
    placed: set[str] = set()
    for path in dashboards:
        config = load(path)
        position = config.get("position") or {}
        if not position:
            errors.append(f"{path.name}: no position block - the dashboard is empty")
        for node in position.values():
            if not isinstance(node, dict) or node.get("type") != "CHART":
                continue
            referenced = (node.get("meta") or {}).get("uuid")
            placed.add(referenced)
            if referenced not in chart_uuids:
                errors.append(
                    f"{path.name}: places chart {referenced} which this bundle "
                    "does not define - the dashboard would import with a hole"
                )
        if not config.get("published"):
            errors.append(f"{path.name}: not published; nobody would find it")

    orphans = set(chart_uuids) - placed
    if orphans:
        errors.append(
            "charts defined but placed on no dashboard: "
            + ", ".join(sorted(chart_uuids[u] for u in orphans))
        )

    print(f"databases: {len(databases)}")
    print(f"datasets:  {len(datasets)}  ({', '.join(sorted(dataset_uuids.values()))})")
    print(f"charts:    {len(charts)}")
    print(f"dashboards:{len(dashboards)}")
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
