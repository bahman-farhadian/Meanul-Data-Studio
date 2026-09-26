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

  3. Does every CHART return anything over the window it actually uses?
     A metric that runs is not a chart that works. time_range "Last week"
     resolves to 00:00 seven days back -> 00:00 TODAY, so the Revenue and
     Completed trips tiles summed a window that excluded the current day
     and showed the seeded figures while a full day of live trips sat
     outside. Every metric passed. A correct sum over a wrong window looks
     exactly like a number, so the window is resolved through Superset's
     own parser and the chart's query is run over it.

What this does NOT claim: that every chart renders. A chart can return the
right rows and still draw badly from a wrong field name in an override.
The bar here is that the data is present, the SQL is valid, and the window
a chart uses actually contains it.

Run through the one-shot:  docker compose run --rm superset-verify
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from superset.app import create_app
from superset.utils.date_parser import get_since_until

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

        # Matching uuids is not the same as matching CONTENT, and the
        # difference is not academic: the uuids are derived from a slug, so
        # they survive every edit to a chart. A Superset still running last
        # week's definitions therefore held all twenty of them and this
        # check said ok - while the dashboard showed a table sorted the
        # wrong way and a title that had been changed two commits earlier.
        #
        # superset import-directory is why it can happen at all: it catches
        # its own failure, logs it, and exits 0. Nothing upstream notices.
        print()
        print("== does what Superset holds still match the files ==")
        stale = 0
        for path in chart_files:
            config = scalars(path)
            slice_ = (
                db.session.query(Slice)
                .filter_by(uuid=config["uuid"])
                .one_or_none()
            )
            if slice_ is None:
                continue
            drift = []
            if slice_.slice_name != config["slice_name"]:
                drift.append(f"name {slice_.slice_name!r} != {config['slice_name']!r}")
            declared = config.get("params") or {}
            stored = json.loads(slice_.params or "{}")
            for field in ("metrics", "row_limit", "legacy_order_by", "time_range",
                          "groupby", "viz_type"):
                if field in declared and stored.get(field) != declared[field]:
                    drift.append(
                        f"{field} {stored.get(field)!r} != {declared[field]!r}"
                    )
            if drift:
                stale += 1
                print(f"  STALE    {config['slice_name']}")
                for line in drift:
                    print(f"           {line}")
        if stale:
            errors.append(
                f"{stale} chart(s) in Superset do not match assets/ - the import "
                "did not apply; superset import-directory exits 0 even when it fails"
            )
        else:
            print(f"  ok       all {len(chart_files)} charts match the files")

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
        print("== every chart, over the window it actually uses ==")
        by_uuid = {scalars(path)["uuid"]: path for path in dataset_files}
        for path in chart_files:
            config = scalars(path)
            dataset = by_uuid.get(config["dataset_uuid"])
            if dataset is None:
                continue
            dconf = scalars(dataset)
            params = config.get("params") or {}
            table, dttm = dconf["table_name"], dconf.get("main_dttm_col")
            expressions = dict(metrics_of(dataset))

            try:
                since, until = get_since_until(params.get("time_range") or "No filter")
            except Exception as exc:  # noqa: BLE001 - an unparseable range is the finding
                print(f"  FAILED   {config['slice_name']}: time_range {exc}")
                errors.append(f"{config['slice_name']}: time_range cannot be parsed")
                continue

            wanted = params.get("metrics") or (
                [params["metric"]] if params.get("metric") else []
            )
            selected = [
                f"{expressions[name]} AS {name}" for name in wanted if name in expressions
            ]
            if not selected:
                continue
            grouping = list(params.get("groupby") or [])
            if params.get("x_axis") and params["x_axis"] not in grouping:
                grouping.insert(0, params["x_axis"])

            where = ["1 = 1"]
            if dttm and since:
                where.append(f"{dttm} >= toDateTime('{since:%Y-%m-%d %H:%M:%S}')")
            if dttm and until:
                where.append(f"{dttm} < toDateTime('{until:%Y-%m-%d %H:%M:%S}')")

            limit = int(params.get("row_limit") or 10000)
            sql = (
                "SELECT " + ", ".join(grouping + selected)
                + f" FROM nus.{table} WHERE " + " AND ".join(where)
                + (f" GROUP BY {', '.join(grouping)}" if grouping else "")
                + f" LIMIT {limit + 1}"
            )
            try:
                frame = database.get_df(sql)
            except Exception as exc:  # noqa: BLE001
                first = str(exc).strip().splitlines()[0][:130]
                print(f"  FAILED   {config['slice_name']}: {first}")
                errors.append(f"{config['slice_name']}: its query does not run - {first}")
                continue

            rows = len(frame)
            window = f"{since:%m-%d %H:%M}..{until:%m-%d %H:%M}" if since else "all time"
            if rows == 0:
                print(f"  EMPTY    {config['slice_name']:<38} [{window}]")
                errors.append(
                    f"{config['slice_name']}: returns nothing over its own window "
                    f"({params.get('time_range')!r}) - the metric runs, the chart is blank"
                )
            elif rows > limit:
                # Not a failure: a top-N table truncates on purpose. Worth
                # saying out loud, because Superset shows the reader a
                # partial-data warning and nothing else explains it.
                print(
                    f"  cut      {config['slice_name']:<38} [{window}]  "
                    f"more than {limit} rows - Superset will say 'partial data'"
                )
            else:
                head = "  ".join(
                    f"{c}={frame.iloc[0][c]:,.2f}" if frame[c].dtype.kind == "f"
                    else f"{c}={frame.iloc[0][c]}"
                    for c in list(frame.columns)[-2:]
                )
                print(f"  ok       {config['slice_name']:<38} [{window}]  {rows:>4} rows  {head}")

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
