"""The Superset bundle is closed, importable, and cannot shadow a column.

Superset keeps charts in its own database, so "provisioned" means imported -
and an import that half-fails is silent. Superset's own importer skips a
chart whose dataset uuid it cannot find, and a dashboard that places a chart
nobody defined imports with a hole in it. Both are caught here, without a
server.

The collision rule below is the one that was actually found the hard way.
Three of the six datasets would not execute a single query, because a metric
named after the column it sums makes ClickHouse resolve the alias first and
the next metric becomes an aggregate inside an aggregate - error 184, and
only when a dashboard selects two of them together.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# this file lives at not-uber-service/z-lib/nus-common/tests/
NUS = TESTS.parents[2]
SUPERSET = NUS / "g-infra-superset"
ASSETS = SUPERSET / "assets"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SUPERSET / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = _load("nus_check_assets", "check-assets.py")
RENDER = _load("nus_render_assets", "render_assets.py")


def is_grid(params: dict) -> bool:
    """A heatmap, whose x_axis carries a dimension rather than time."""
    return str(params.get("viz_type") or "").startswith("heatmap")


def test_the_bundle_passes_its_own_check() -> None:
    assert CHECK.check() == []


def test_a_metric_named_after_its_column_is_refused(tmp_path) -> None:
    """The failing direction of the rule that cost three datasets."""
    bad = RENDER.dataset(
        "probe_table",
        description="a dataset whose metric shadows its own column",
        main_dttm="hour",
        comment="written only to be rejected",
        columns=[RENDER.column("hour", "DATETIME", dttm=True),
                 RENDER.column("revenue", "DECIMAL")],
        metrics=[RENDER.metric("revenue", "Revenue", "sum(revenue)", fmt="$,.0f")],
    )
    written = tmp_path / "probe_table.yaml"
    written.write_text(
        RENDER.to_yaml({k: v for k, v in bad.items() if not k.startswith("_")})
    )
    columns = CHECK.columns_of(written)
    names = [name for name, _ in CHECK.metrics_of(written)]
    assert names == ["revenue"], names
    assert "revenue" in columns, columns


def test_no_shipped_metric_shadows_a_column() -> None:
    for path in sorted(ASSETS.glob("datasets/*/*.yaml")):
        columns = CHECK.columns_of(path)
        for metric_name, _ in CHECK.metrics_of(path):
            assert metric_name not in columns, (
                f"{path.name}: metric {metric_name!r} shadows a column"
            )


def test_every_ratio_sums_before_dividing() -> None:
    for path in sorted(ASSETS.glob("datasets/*/*.yaml")):
        for metric_name, expression in CHECK.metrics_of(path):
            if "/" not in expression:
                continue
            left, _, right = expression.partition("/")
            assert CHECK.AGGREGATE.search(left) and CHECK.AGGREGATE.search(right), (
                f"{path.name}: {metric_name!r} divides raw columns: {expression!r}"
            )
            assert not CHECK.AVERAGE.search(expression), (
                f"{path.name}: {metric_name!r} averages a partial sum"
            )


def test_every_chart_is_placed_and_every_placed_chart_exists() -> None:
    charts = {
        CHECK.load(path)["uuid"]: CHECK.load(path)["slice_name"]
        for path in sorted(ASSETS.glob("charts/*.yaml"))
    }
    placed = set()
    for path in sorted(ASSETS.glob("dashboards/*.yaml")):
        for node in (CHECK.load(path)["position"] or {}).values():
            if isinstance(node, dict) and node.get("type") == "CHART":
                placed.add(node["meta"]["uuid"])
    assert placed, "no charts placed on any dashboard"
    assert placed <= set(charts), f"dashboard places charts that do not exist: {placed - set(charts)}"
    assert set(charts) <= placed, (
        "charts defined but on no dashboard: "
        f"{sorted(charts[u] for u in set(charts) - placed)}"
    )


def test_ids_are_derived_from_the_same_namespace_as_the_connection() -> None:
    """The bundle and register_database.py must agree on the connection uuid.

    They are computed independently, in two files. If they ever disagree the
    import creates a second connection and every chart points at the wrong
    one - which looks like a credentials problem, not an id problem.
    """
    namespace = uuid.uuid5(
        uuid.NAMESPACE_URL, "https://github.com/bahman-farhadian/Meanul-Data-Studio"
    )
    expected = str(uuid.uuid5(namespace, "database/clickhouse"))
    written = CHECK.load(ASSETS / "databases" / "clickhouse_nus.yaml")["uuid"]
    assert written == expected

    registrar = (SUPERSET / "init" / "register_database.py").read_text()
    assert 'uuid.uuid5(NAMESPACE, "database/clickhouse")' in registrar
    assert "https://github.com/bahman-farhadian/Meanul-Data-Studio" in registrar


def test_the_committed_yaml_is_what_the_generator_writes() -> None:
    """Hand-editing the bundle makes the generator a lie."""
    for document in [RENDER.METADATA, RENDER.DATABASE, *RENDER.DATASETS,
                     *RENDER.CHARTS, *RENDER.DASHBOARDS]:
        document = dict(document)
        path = ASSETS / document.pop("_path")
        comment = document.pop("_comment", None)
        assert path.is_file(), f"{path.name} was never rendered"
        header = "".join(f"# {line}\n" for line in (comment or "").splitlines())
        assert path.read_text() == header + RENDER.to_yaml(document), (
            f"{path.name} differs from render_assets.py - run it rather than "
            "editing the YAML"
        )


def test_the_import_runs_before_the_credentials_are_registered() -> None:
    """Reversing these two made every chart fail with ClickHouse 516 once.

    The bundle's database file carries a uri with no password, because a
    password does not belong in a committed file. --overwrite applies that
    uri to the connection, so whatever password was there is lost. Only
    registering afterwards puts the real one back.
    """
    script = (SUPERSET / "init" / "init-superset.sh").read_text()
    import_at = script.index("import-directory")
    register_at = script.index("register_database.py")
    assert import_at < register_at, (
        "register_database.py must run AFTER the asset import"
    )


def test_the_assets_are_mounted_and_verified() -> None:
    compose = (SUPERSET / "docker-compose.yaml").read_text()
    assert "./assets:/app/assets:ro" in compose, "assets/ is not mounted read-only"
    assert "verify-assets.py" in compose, "there is no one-shot that verifies the import"

    root = (NUS / "Makefile").read_text()
    assert "verify-superset" in root, "make verify-dash does not check the assets"


def test_every_table_states_the_sort_its_title_promises() -> None:
    """A row limit without a sort keeps an arbitrary slice.

    "Where demand goes unserved" shipped ordering by requests ASCENDING and
    listed the twenty-five quietest zones - the opposite of its title, with
    no error anywhere to say so.
    """
    seen = 0
    for path in sorted(ASSETS.glob("charts/*.yaml")):
        params = CHECK.load(path).get("params") or {}
        if params.get("viz_type") != "table":
            continue
        seen += 1
        metrics = params["metrics"]
        stated = {
            params.get(k) for k in
            ("series_limit_metric", "legacy_order_by", "timeseries_limit_metric")
            if params.get(k)
        }
        assert stated, f"{path.name} states no sort metric"
        assert len(stated) == 1, f"{path.name} spells the sort two ways: {stated}"
        assert stated == {metrics[0]}, (
            f"{path.name} sorts by {stated} but metrics[0] is {metrics[0]!r}"
        )
    # Two, not three: the origin-destination leaderboard became a grid,
    # because no row limit a top-N list can carry escapes Superset's
    # partial-data warning. See test_every_grid_carries_a_limit_it_cannot_reach.
    assert seen >= 2, f"expected the two leaderboards, found {seen}"


def test_a_table_with_no_sort_is_refused(tmp_path) -> None:
    """The failing direction of the rule."""
    bad = RENDER.chart(
        "probe", name="Probe", viz="table", dataset_name="fulfilment_hourly",
        description="a table that keeps an arbitrary slice",
        params={"query_mode": "aggregate", "groupby": ["pickup_zone_id"],
                "metrics": ["requests"], "row_limit": 25,
                "granularity_sqla": "hour", "time_range": "Last week"},
    )
    written = tmp_path / "charts" / "probe.yaml"
    written.parent.mkdir()
    written.write_text(
        RENDER.to_yaml({k: v for k, v in bad.items() if not k.startswith("_")})
    )
    params = CHECK.load(written)["params"]
    assert params["viz_type"] == "table"
    assert not any(
        params.get(k) for k in
        ("series_limit_metric", "legacy_order_by", "timeseries_limit_metric")
    ), "the probe chart was supposed to have no sort"


def test_every_chart_bounds_what_it_scans() -> None:
    """A time range that names no column is silently not applied."""
    for path in sorted(ASSETS.glob("charts/*.yaml")):
        params = CHECK.load(path).get("params") or {}
        window = params.get("time_range")
        assert window and window != "No filter", (
            f"{path.name} scans the table's whole retention"
        )
        assert params.get("granularity_sqla") or params.get("x_axis"), (
            f"{path.name} sets time_range {window!r} but names no time column"
        )


def test_the_window_reaches_the_present() -> None:
    """A friendly time range is not a harmless one.

    Resolved against apache/superset 6.1.0, "Last week" AND "Last 7 days"
    both give 00:00 seven days back -> 00:00 TODAY. The upper bound is
    midnight, so a daily-grain chart silently drops the current day: the
    Revenue and Completed trips tiles read exactly the seeded day's figures
    while a full day of live trips sat outside the window.

    A correct sum over a wrong window looks like a number, so this is
    checked rather than remembered.
    """
    ends_at_midnight = {"Last day", "Last week", "Last 7 days", "Last month",
                        "Last quarter", "Last year"}
    for path in sorted(ASSETS.glob("charts/*.yaml")):
        window = (CHECK.load(path).get("params") or {}).get("time_range")
        assert window not in ends_at_midnight, (
            f"{path.name} uses {window!r}, which ends at midnight today and "
            "drops the current day"
        )
        assert window.rstrip().endswith(": now"), (
            f"{path.name} uses {window!r}; the window has to reach now"
        )


def test_the_time_column_is_the_datasets_own() -> None:
    """Read from the dataset, never repeated, so the two cannot disagree."""
    dttm = {}
    for path in sorted(ASSETS.glob("datasets/*/*.yaml")):
        config = CHECK.load(path)
        dttm[config["uuid"]] = config["main_dttm_col"]
    for path in sorted(ASSETS.glob("charts/*.yaml")):
        config = CHECK.load(path)
        params = config.get("params") or {}
        expected = dttm[config["dataset_uuid"]]
        # A grid spends x_axis on a DIMENSION, not on time - pickup zone
        # across, dropoff zone down - so granularity_sqla is the only key
        # naming its window. Checking x_axis against the time column there
        # would be checking the wrong key, and requiring a time axis would
        # mean the chart could not exist at all.
        keys = ("granularity_sqla",) if is_grid(params) else ("granularity_sqla", "x_axis")
        for key in keys:
            if params.get(key):
                assert params[key] == expected, (
                    f"{path.name} uses {key}={params[key]!r} but its dataset's "
                    f"time column is {expected!r}"
                )


def test_every_superset_table_pages_rather_than_scrolls() -> None:
    seen = 0
    for path in sorted(ASSETS.glob("charts/*.yaml")):
        params = CHECK.load(path).get("params") or {}
        if params.get("viz_type") != "table":
            continue
        seen += 1
        assert params.get("page_length"), f"{path.name} has no page_length"
        assert params["page_length"] <= params["row_limit"], (
            f"{path.name} pages {params['page_length']} of a {params['row_limit']} "
            "row limit, which is not a page"
        )
    assert seen >= 2, f"expected the two leaderboards, found {seen}"


def test_every_grid_carries_a_limit_it_cannot_reach() -> None:
    """Superset's partial-data warning is about the LIMIT, not the data.

    It fires when the rows returned reach row_limit, so a grid is only quiet
    while that limit is unreachable. "Big enough for today's seed" is not
    the bar - the OD chart was a top-100 table and warned on every load,
    and raising it to 6,000 of 7,000 pairs would have warned just the same.

    Two zone dimensions can produce TLC_ZONES^2 cells and not one more, and
    that ceiling does not move with fleet size, trip volume or HISTORY_DAYS.
    So the limit can be unreachable by construction rather than by luck, and
    this is the test that keeps it that way.
    """
    ceiling = CHECK.TLC_ZONES * CHECK.TLC_ZONES
    grids = 0
    for path in sorted(ASSETS.glob("charts/*.yaml")):
        params = CHECK.load(path).get("params") or {}
        if not is_grid(params):
            continue
        dims = [d for d in [params.get("x_axis"),
                            *(params.get("groupby") or [])] if d]
        assert len(dims) == 2, f"{path.name}: a grid needs two dimensions, got {dims}"
        if not all(d.endswith("_zone_id") for d in dims):
            continue
        grids += 1
        assert params["row_limit"] > ceiling, (
            f"{path.name}: row_limit {params['row_limit']:,} is reachable "
            f"within the {ceiling:,} pairs {CHECK.TLC_ZONES} zones allow, so "
            "Superset will warn about partial data"
        )
    assert grids, "no zone-against-zone grid found - has the OD chart moved?"


def test_the_od_chart_is_not_a_top_n_list_again() -> None:
    """The failing direction, against the shape that actually shipped.

    The chart really was `table(..., sort_by="trips", row_limit=100)` over
    pickup x dropoff, and it warned about partial data on every load for
    exactly the reason above. Rebuilt here so the checker is proved against
    the mistake rather than against an invented one.
    """
    bad = RENDER.chart(
        "probe", name="Top 100 origin-destination pairs", viz="table",
        dataset_name="od_matrix_daily",
        description="the shape that warned on every load",
        params={"query_mode": "aggregate",
                "groupby": ["pickup_zone_id", "dropoff_zone_id"],
                "metrics": ["trips"], "row_limit": 100, "page_length": 15,
                "legacy_order_by": "trips", "timeseries_limit_metric": "trips",
                "granularity_sqla": "day", "time_range": RENDER.TIME_RANGE},
    )
    pairs = RENDER.TLC_ZONES * RENDER.TLC_ZONES
    assert bad["params"]["row_limit"] < pairs, (
        "the probe was supposed to carry a reachable limit"
    )
    live = CHECK.load(ASSETS / "charts" / "od-leaderboard.yaml")["params"]
    assert live["viz_type"].startswith("heatmap"), (
        "the OD chart went back to being a ranked list"
    )
    assert live["row_limit"] > pairs
