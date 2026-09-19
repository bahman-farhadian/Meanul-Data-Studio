"""The root assessment contract must stay aligned with shipped checks.

Reads ASSESSMENT.md and the SQL / geo / Makefile sources it cites.
Numbers and `make` target names are taken from those files, not copied
into this test as literals.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# this file lives at not-uber-service/z-lib/nus-common/tests/
NUS = TESTS.parents[2]
ROOT = TESTS.parents[3]
STANDARD = ROOT / "ASSESSMENT.md"


def _standard() -> str:
    assert NUS.name == "not-uber-service", NUS
    assert STANDARD.is_file(), f"missing assessment contract: {STANDARD}"
    return STANDARD.read_text()


def _on_network_sql() -> str:
    path = NUS / "z-config" / "check-on-network.sql"
    return path.read_text()


def _geo_source() -> str:
    return (NUS / "z-lib" / "nus-common" / "nus_common" / "geo.py").read_text()


def _makefile_targets() -> set[str]:
    root_make = NUS / "Makefile"
    texts = [root_make.read_text()]
    for inc in re.finditer(r"^include\s+(\S+)", texts[0], re.M):
        path = NUS / inc.group(1)
        if path.is_file():
            texts.append(path.read_text())
    names: set[str] = set()
    for text in texts:
        for match in re.finditer(r"^\.PHONY:\s*(.+)$", text, re.M):
            names.update(match.group(1).split())
        for match in re.finditer(r"^([a-zA-Z][a-zA-Z0-9_-]*):", text, re.M):
            names.add(match.group(1))
    return names


def test_standard_lists_every_lettered_component():
    text = _standard()
    dirs = sorted(
        p.name
        for p in NUS.iterdir()
        if p.is_dir()
        and not p.name.startswith(".")
        and (re.match(r"^[a-o]-", p.name) or p.name in {"z-config", "z-lib"})
    )
    assert dirs, f"no lettered components under {NUS}"
    missing = [name for name in dirs if name not in text]
    assert missing == [], f"ASSESSMENT.md missing components: {missing}"


def test_off_network_metres_match_shipped_sql():
    sql = _on_network_sql()
    pickup = re.search(r"pickup_m > (\d+)", sql)
    dropoff = re.search(r"dropoff_m > (\d+)", sql)
    assert pickup and dropoff, sql
    metres = pickup.group(1)
    assert metres == dropoff.group(1)
    text = _standard()
    assert f"{metres} m" in text or f"{metres}m" in text
    assert metres in text


def test_long_two_point_route_matches_shipped_sql():
    sql = _on_network_sql()
    npoints = re.search(r"ST_NPoints\(route\)\s*<=\s*(\d+)", sql)
    length_m = re.search(r"ST_Length\(route::geography\)\s*>\s*(\d+)", sql)
    assert npoints and length_m, sql
    text = _standard()
    assert npoints.group(1) in text
    assert "ST_NPoints" in text or "NPoints" in text
    metres = length_m.group(1)
    assert metres in text
    km = int(metres) / 1000
    assert km == int(km)
    assert f"{int(km)} km" in text


def test_chord_min_km_matches_shipped_geo():
    geo = _geo_source()
    match = re.search(
        r"def is_chord_path\([^)]*min_km:\s*float\s*=\s*([0-9.]+)",
        geo,
    )
    assert match, geo
    min_km = match.group(1)
    text = _standard()
    assert min_km in text
    assert "is_chord_path" in text
    assert f"{min_km} km" in text or f"min_km={min_km}" in text or f"min_km = {min_km}" in text


def test_named_make_targets_exist():
    text = _standard()
    named = set(re.findall(r"make ([a-z][a-z0-9_-]*)", text))
    assert named, "ASSESSMENT.md names no make targets"
    targets = _makefile_targets()
    missing = sorted(name for name in named if name not in targets)
    assert missing == [], f"ASSESSMENT.md names make targets that do not exist: {missing}"


def test_live_walk_and_tile_terms_are_in_the_sources():
    text = _standard()
    ids = (NUS / "z-lib" / "nus-common" / "nus_common" / "ids.py").read_text()
    redis = (NUS / "z-lib" / "nus-common" / "nus_common" / "redis_client.py").read_text()
    driver = (NUS / "j-service-driver" / "driver_service" / "__main__.py").read_text()
    haproxy = (NUS / "z-config" / "haproxy" / "haproxy.cfg.template").read_text()
    tiles_make = (NUS / "f-infra-grafana" / "Makefile").read_text()

    assert "drv-" in ids and "drv-" in text
    assert "psg-" in ids and "psg-" in text
    assert "trp-" in ids and "trp-" in text
    assert "SIM_TIMEZONE" in _geo_source() and "SIM_TIMEZONE" in text
    assert "trip_active" in redis and "trip_active" in text
    assert "path_chord" in driver and "path_p50" in driver
    assert "path_chord" in text and "path_p50" in text
    assert "/tiles/" in haproxy and "/tiles/" in text
    assert "tiles-health" in tiles_make and "make tiles-health" in text
    assert "image/png" in tiles_make and "image/png" in text
