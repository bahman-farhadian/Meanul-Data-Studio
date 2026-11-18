"""Redis piece README must name the shipped image pin.

Reads b-infra-redis/docker-compose.yaml for the default REDIS_IMAGE, then
the README. The tag is not copied into this test as a literal oracle.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
HAILPATH = TESTS.parents[2]


def _compose_redis_image_pin() -> str:
    compose = (HAILPATH / "b-infra-redis" / "docker-compose.yaml").read_text()
    match = re.search(r"image:\s*\$\{REDIS_IMAGE:-([^}]+)\}", compose)
    assert match, "compose is missing a ${REDIS_IMAGE:-...} default"
    return match.group(1)


def test_redis_readme_names_compose_image_pin():
    pin = _compose_redis_image_pin()
    readme = (HAILPATH / "b-infra-redis" / "README.md").read_text()
    assert pin in readme, f"README does not name compose pin {pin}"
    assert "one process" in readme.lower()
    assert "640m" in readme and "384mb" in readme
    assert "4g" in readme and "2560mb" in readme
    assert "PROXY_REDIS_PORT" in readme
    assert "hailpath-redis" in readme
