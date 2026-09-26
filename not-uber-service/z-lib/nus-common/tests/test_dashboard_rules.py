"""The dashboard rules refuse what they are supposed to refuse.

check-dashboards.py gained two structural rules with the marketplace tier:
every rate is sum(x)/sum(y), and no chart carries two units. A rule that
only ever runs against files that already pass is a rule nobody has seen
work - the same vacuous-pass problem Q0 exists to stop in the quality bars,
so both are exercised here in the failing direction as well as the passing
one.

Also checked: the committed JSON is exactly what render_dashboards.py
produces. The JSON is generated, and a panel edited in the browser and
pasted back would pass every other check while quietly becoming the source
of truth.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# this file lives at not-uber-service/z-lib/nus-common/tests/
NUS = TESTS.parents[2]
GRAFANA = NUS / "f-infra-grafana"
JSON_DIR = GRAFANA / "provisioning" / "dashboards" / "json"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, GRAFANA / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = _load("nus_check_dashboards", "check-dashboards.py")
RENDER = _load("nus_render_dashboards", "render_dashboards.py")
PROBE = _load("nus_panel_probe", "panel-probe.py")


def test_a_rate_of_raw_columns_is_refused() -> None:
    """Two partial sums divided is not a rate, whatever it looks like."""
    bad = "SELECT completed / trips_ended AS r FROM nus.fulfilment_hourly"
    assert CHECK._check_ratios("x", 1, bad), "dividing raw rollup columns was accepted"


def test_an_average_of_a_rate_is_refused() -> None:
    bad = "SELECT avg(utilization) AS u FROM nus.driver_utilization_hourly"
    errors = CHECK._check_ratios("x", 1, bad)
    assert any("avg()" in e for e in errors), errors


def test_sum_over_sum_is_accepted() -> None:
    good = (
        "SELECT sum(completed) / sum(trips_ended) AS r "
        "FROM nus.fulfilment_hourly"
    )
    assert CHECK._check_ratios("x", 1, good) == []


def test_a_cast_around_the_sums_is_still_accepted() -> None:
    """Take rate divides a Float64 cast of two exact Decimal sums."""
    good = (
        "SELECT toFloat64(sum(revenue) - sum(payout_total)) / "
        "toFloat64(sum(revenue)) AS take_rate FROM nus.trip_stats_hourly"
    )
    assert CHECK._check_ratios("x", 1, good) == []


def test_two_units_on_one_chart_are_visible_to_the_checker() -> None:
    mixed = {
        "type": "timeseries",
        "fieldConfig": {
            "defaults": {"unit": "s"},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "revenue"},
                    "properties": [{"id": "unit", "value": "currencyUSD"}],
                }
            ],
        },
    }
    assert CHECK._chart_units(mixed) == {"s", "currencyUSD"}


def test_overlapping_panels_are_refused() -> None:
    """Grafana reflows an overlap instead of refusing it, so the check must."""
    panels = [
        {"id": 1, "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}},
        {"id": 2, "gridPos": {"x": 6, "y": 4, "w": 12, "h": 8}},
    ]
    assert CHECK._check_layout("x", panels), "an overlap was accepted"


def test_panels_side_by_side_are_accepted() -> None:
    panels = [
        {"id": 1, "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}},
        {"id": 2, "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8}},
        {"id": 3, "gridPos": {"x": 0, "y": 8, "w": 24, "h": 8}},
    ]
    assert CHECK._check_layout("x", panels) == []


def test_two_panels_with_one_id_are_refused() -> None:
    panels = [
        {"id": 1, "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}},
        {"id": 1, "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8}},
    ]
    assert any("share id" in e for e in CHECK._check_layout("x", panels))


def test_every_charting_panel_states_a_unit() -> None:
    """Across every dashboard, not only the new one."""
    for path in sorted(JSON_DIR.glob("*.json")):
        dash = json.loads(path.read_text())
        for panel in dash.get("panels") or []:
            if panel.get("type") not in CHECK.CHARTS:
                continue
            assert CHECK._chart_units(panel), (
                f"{dash['uid']} panel {panel['id']} ({panel['title']!r}) "
                "declares no unit"
            )


def test_every_marketplace_panel_states_what_it_means() -> None:
    dash = json.loads((JSON_DIR / "nus-marketplace.json").read_text())
    panels = dash.get("panels") or []
    assert panels, "nus-marketplace has no panels"
    for panel in panels:
        assert (panel.get("description") or "").strip(), (
            f"panel {panel['id']} ({panel['title']!r}) has no description"
        )


def test_the_committed_json_is_what_the_generator_writes() -> None:
    """The JSON is generated. Hand-editing it makes the generator a lie."""
    for dash in RENDER.ALL:
        path = JSON_DIR / f"{dash['uid']}.json"
        assert path.is_file(), f"{path.name} was never rendered"
        assert json.loads(path.read_text()) == dash, (
            f"{path.name} differs from render_dashboards.py - "
            "run it rather than editing the JSON"
        )


def test_the_marketplace_dashboard_passes_the_full_check() -> None:
    assert CHECK.check() == []


def test_the_probe_covers_every_panel_that_carries_a_query() -> None:
    """A prober that silently skips panels is worse than none."""
    expected = 0
    for path in sorted(JSON_DIR.glob("*.json")):
        dash = json.loads(path.read_text())
        for panel in dash.get("panels") or []:
            for target in panel.get("targets") or []:
                if (target.get("rawSql") or "").strip():
                    expected += 1
    assert expected > 0

    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert PROBE.main() == 0
    sql = out.getvalue()
    assert sql.count("AS panel FORMAT TSVRaw;") == expected
    assert f"=== {expected} panels ran without error ===" in sql


def test_no_grafana_macro_survives_expansion() -> None:
    """A macro left in place is a syntax error, not a panel that runs."""
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        PROBE.main()
    sql = out.getvalue()
    for macro in ("$__timeFilter", "$__timeInterval", "$__fromTime", "$__toTime", "${"):
        assert macro not in sql, f"{macro} was not expanded"


def test_a_rollup_panel_may_not_be_quiet_but_a_live_one_may() -> None:
    """The exemption is the panel's own window, not a list to maintain."""
    assert PROBE.may_be_quiet("SELECT * FROM nus.trip_events WHERE event_time >= now() - INTERVAL 2 MINUTE")
    assert PROBE.may_be_quiet("SELECT * FROM nus.hotspot_history WHERE zone_id = '${zone_id}'")
    assert not PROBE.may_be_quiet(
        "SELECT sum(completed) / sum(trips_ended) FROM nus.fulfilment_hourly "
        "WHERE $__timeFilter(hour)"
    )

    marketplace = json.loads((JSON_DIR / "nus-marketplace.json").read_text())
    for panel in marketplace["panels"]:
        sql = (panel.get("targets") or [{}])[0].get("rawSql") or ""
        assert not PROBE.may_be_quiet(sql), (
            f"marketplace panel {panel['id']} would be exempt from the "
            "non-empty bar; every one of them reads a rollup"
        )


def test_the_panel_probe_is_wired_into_verification() -> None:
    root = (NUS / "Makefile").read_text()
    assert re.search(r"^verify-dash:.*\bverify-grafana\b", root, re.M), (
        "make verify-dash does not run the panel probe"
    )
    grafana_make = (NUS / "f-infra-grafana" / "Makefile").read_text()
    assert "panel-probe.py" in grafana_make


def test_a_substituted_variable_carries_the_distributed_setting() -> None:
    """Every IN-subquery the probe builds must survive two shards.

    A real panel filters on a literal and touches one Distributed table.
    Substituting the template variable makes it a Distributed table inside a
    subquery of a Distributed query, which ClickHouse denies outright -
    code 288, and only ever on two shards or more, so a single-node probe
    would never see it. Reproduced against a real two-shard cluster.
    """
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        PROBE.main()
    statements = [s for s in out.getvalue().split(";") if s.strip()]

    with_subquery = [s for s in statements if re.search(r"(?i)\bIN\s*\(\s*SELECT", s)]
    assert with_subquery, "no panel substituted a template variable"
    for statement in with_subquery:
        assert "distributed_product_mode" in statement, (
            "an IN-subquery statement carries no distributed_product_mode; "
            "it would be denied on two shards"
        )

    for statement in statements:
        if "distributed_product_mode" in statement:
            assert re.search(r"(?i)\bIN\s*\(\s*SELECT", statement), (
                "the setting is on a statement that does not need it"
            )


def test_the_setting_is_global_not_local() -> None:
    """local is only right when both tables shard on the same key.

    rider_positions shards on rider_id while the nus-trip variable reads
    trip_events sharded on trip_id, so local would quietly drop rows rather
    than fail. global costs a pass and is correct whatever the sharding.
    """
    assert "'global'" in PROBE.SUBQUERY_SETTINGS
    assert "'local'" not in PROBE.SUBQUERY_SETTINGS
