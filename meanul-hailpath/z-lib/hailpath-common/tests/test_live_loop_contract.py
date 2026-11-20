"""Shipped make targets, laptop seed, and live-ops panels stay aligned.

Reads the Makefile, .env.example, and hailpath-live-ops JSON. Does not
copy those numbers into this file as the only oracle.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
HAILPATH = TESTS.parents[2]


def _makefile() -> str:
    return (HAILPATH / "Makefile").read_text()


def _included_makefiles() -> str:
    root = _makefile()
    parts = [root]
    for inc in re.finditer(r"^include\s+(\S+)", root, re.M):
        path = HAILPATH / inc.group(1)
        if path.is_file():
            parts.append(path.read_text())
    return "\n".join(parts)


def _example() -> str:
    return (HAILPATH / ".env.example").read_text()


def test_live_loop_make_targets_exist():
    text = _included_makefiles()
    for name in (
        "cdc-register",
        "verify-cdc",
        "lag",
        "verify-positions",
        "verify-grafana",
    ):
        assert re.search(rf"^{re.escape(name)}\s*:", text, re.M), name


def test_services_and_groups_match_the_makefile():
    make = _makefile()
    services = re.search(r"^SERVICES\s*:?=\s*(.+)$", make, re.M)
    groups = re.search(r"^GROUPS\s*:?=\s*(.+)$", make, re.M)
    assert services and groups, make
    service_names = services.group(1).split()
    group_names = groups.group(1).split()
    expected_services = [
        "hailpath-cache-updater",
        "hailpath-driver",
        "hailpath-passenger",
        "hailpath-dispatch",
        "hailpath-city",
        "hailpath-clickhouse-sink",
        "hailpath-archiver",
    ]
    expected_groups = [
        "hailpath-cache-updater",
        "hailpath-driver",
        "hailpath-passenger",
        "hailpath-dispatch",
        "hailpath-city",
        "hailpath-clickhouse-sink",
    ]
    assert service_names == expected_services
    assert group_names == expected_groups
    assert "hailpath-archiver" not in group_names


def test_env_example_pins_laptop_seed_and_service_ceilings():
    example = _example()

    def pin(name: str) -> str:
        match = re.search(rf"^{re.escape(name)}=(.*)$", example, re.M)
        assert match, name
        return match.group(1).strip()

    assert pin("SEED_DRIVERS") == "1060"
    assert pin("SEED_PASSENGERS") == "15000"
    assert pin("HISTORY_DAYS") == "1"
    assert pin("HISTORY_TRIPS_PER_DAY") == "6550"
    assert pin("SEED_DRIVERS") != "106000"
    assert pin("CACHE_UPDATER_MEM") == "448m"
    assert pin("DRIVER_SERVICE_MEM") == "512m"
    assert pin("PASSENGER_SERVICE_MEM") == "448m"
    assert pin("DISPATCH_SERVICE_MEM") == "320m"
    assert pin("CITY_SERVICE_MEM") == "320m"
    assert pin("CLICKHOUSE_SINK_MEM") == "512m"
    assert pin("ARCHIVER_SERVICE_MEM") == "256m"
    assert pin("CACHE_UPDATER_CPUS") == "0.5"
    assert pin("DRIVER_SERVICE_CPUS") == "1"
    assert pin("PASSENGER_SERVICE_CPUS") == "0.75"
    assert pin("DISPATCH_SERVICE_CPUS") == "0.5"
    assert pin("CITY_SERVICE_CPUS") == "0.5"
    assert pin("CLICKHOUSE_SINK_CPUS") == "0.75"
    assert pin("ARCHIVER_SERVICE_CPUS") == "0.25"


def test_live_ops_json_has_ingest_and_lag_panels():
    path = (
        HAILPATH
        / "f-infra-grafana"
        / "provisioning"
        / "dashboards"
        / "json"
        / "hailpath-live-ops.json"
    )
    dash = json.loads(path.read_text())
    titles = [p.get("title", "") for p in dash.get("panels") or []]
    blob = "\n".join(titles)
    assert "Trip events per minute" in titles
    assert "Driver positions per minute" in titles
    assert any("trip_events lag" in t for t in titles), titles
    assert any("last-known lag" in t or "driver_positions lag" in t for t in titles), titles
    assert "Trip events per minute" in blob
