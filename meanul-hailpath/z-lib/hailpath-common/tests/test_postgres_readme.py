"""Postgres piece README must name the shipped image pin.

Reads a-infra-postgres/docker-compose.yaml for the default PG_IMAGE, then
the README. The tag is not copied into this test as a literal oracle.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
HAILPATH = TESTS.parents[2]


def _compose_pg_image_pin() -> str:
    compose = (HAILPATH / "a-infra-postgres" / "docker-compose.yaml").read_text()
    match = re.search(r"image:\s*\$\{PG_IMAGE:-([^}]+)\}", compose)
    assert match, "compose is missing a ${PG_IMAGE:-...} default"
    return match.group(1)


def test_postgres_readme_names_compose_image_pin():
    pin = _compose_pg_image_pin()
    readme = (HAILPATH / "a-infra-postgres" / "README.md").read_text()
    assert pin in readme, f"README does not name compose pin {pin}"
    assert "one process" in readme.lower()
    assert "3g" in readme and "16g" in readme
    assert "laptop" in readme.lower()
    assert "deploy" in readme.lower()
    assert "postgres:18.6" not in readme
