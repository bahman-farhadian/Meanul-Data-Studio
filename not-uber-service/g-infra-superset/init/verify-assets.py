"""Prove the imported assets are all there AND that every metric runs.

The bar this replaces was "Superset answers /health", which an empty
Superset passes perfectly - and did, for the whole of this project so far.

Two questions, both of which have to be yes:

  1. Is every dataset, chart and dashboard in assets/ actually inside
     Superset? An import that half-failed leaves a dashboard with holes in
     it and says nothing; Superset's own importer skips a chart whose
     dataset it could not find, silently.

  2. Does every metric expression actually RUN against the warehouse?
     A metric is SQL text nobody has executed until a human opens the
     chart. quantileMerge over an AggregateFunction column, a Decimal cast,
     a column renamed in the DDL - each of those is a chart that renders an
     error to whoever opens it first. Running them here, one query per
     dataset, moves that discovery into the deploy.

What this does NOT claim: that every chart renders. A chart can have a
correct metric and still come back empty from a time range or a filter.
The bar here is that the data is present and the SQL is valid, which is
what a machine can honestly check.

Run through the one-shot:  docker compose run --rm superset-verify
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from superset.app import create_app

ASSETS = Path("/app/assets")

# How far back to ask. Long enough that a stack up for a few minutes has
# something, short enough that the query stays cheap on a full warehouse.
WINDOWS = {
    "hour": "{col} >= now() - INTERVAL 24 HOUR",
    "day": "{col} >= today() - 7",
}


def scalars(path: Path) -> dict:
    """The top-level keys of one of our own generated YAML files."""
    document: dict = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "-")):
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
            document[key] = raw
    return document


def metrics_of(path: Path) -> list[tuple[str, str]]:
    found, name = [], None
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- metric_name:"):
            name = stripped.split(":", 1)[1].strip().strip("'")
        elif stripped.startswith("expression:") and name:
            expression = stripped.split(":", 1)[1].strip()
            found.append((name, expression[1:-1].replace("''", "'")))
            name = None
    return found


def main() -> int:
    errors: list[str] = []
    app = create_app()
    with app.app_context():
        from superset import db
        from superset.connectors.sqla.models import SqlaTable
        from superset.models.core import Database
        from superset.models.dashboard import Dashboard
        from superset.models.slice import Slice

        dataset_files = sorted(ASSETS.glob("datasets/*/*.yaml"))
        chart_files = sorted(ASSETS.glob("charts/*.yaml"))
        dashboard_files = sorted(ASSETS.glob("dashboards/*.yaml"))
        if not (dataset_files and chart_files and dashboard_files):
            print(f"FAIL\n  nothing to verify under {ASSETS}")
            return 1

        print("== what the bundle declares, and what Superset holds ==")
        for label, files, model in (
            ("datasets", dataset_files, SqlaTable),
            ("charts", chart_files, Slice),
            ("dashboards", dashboard_files, Dashboard),
        ):
            want = {scalars(path)["uuid"] for path in files}
            have = {str(uuid) for (uuid,) in db.session.query(model.uuid).all()}
            missing = want - have
            print(f"  {label:<11} declared {len(want):>3}   in superset {len(have):>3}")
            for uuid in sorted(missing):
                name = next(
                    scalars(p).get("table_name")
                    or scalars(p).get("slice_name")
                    or scalars(p).get("dashboard_title")
                    for p in files
                    if scalars(p)["uuid"] == uuid
                )
                print(f"    MISSING  {name} ({uuid})")
                errors.append(f"{label[:-1]} {name} was not imported")

        database = (
            db.session.query(Database)
            .filter_by(database_name="ClickHouse (nus)")
            .one_or_none()
        )
        if database is None:
            print("FAIL\n  the ClickHouse connection is not registered")
            return 1

        print()
        print("== every metric, executed against the warehouse ==")
        for path in dataset_files:
            config = scalars(path)
            table = config["table_name"]
            dttm = config.get("main_dttm_col")
            where = WINDOWS.get(dttm, "1 = 1").format(col=dttm)
            selected = ", ".join(
                f"{expression} AS {name}" for name, expression in metrics_of(path)
            )
            sql = f"SELECT count() AS rows_seen, {selected} FROM nus.{table} WHERE {where}"
            try:
                frame = database.get_df(sql)
            except Exception as exc:  # noqa: BLE001 - any failure is the finding
                first = str(exc).strip().splitlines()[0][:160]
                print(f"  FAILED   {table}: {first}")
                errors.append(f"{table}: a metric expression does not run - {first}")
                continue
            row = frame.iloc[0].to_dict()
            seen = int(row.pop("rows_seen", 0))
            summary = "  ".join(
                f"{name}={value:,.2f}" if isinstance(value, float) else f"{name}={value}"
                for name, value in list(row.items())[:4]
            )
            state = "ok     " if seen else "NO DATA"
            print(f"  {state}  {table:<34} rows={seen:<9,} {summary}")
            if not seen:
                errors.append(
                    f"{table}: no rows in the window - a chart over it renders empty"
                )

    print()
    if errors:
        print("FAIL")
        for err in errors:
            print(f"  {err}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
