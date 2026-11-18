"""Kafka piece README must name the shipped image pins.

Reads c-infra-kafka/docker-compose.yaml for default image tags, then the
README. Tags are not copied into this test as literal oracles.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
HAILPATH = TESTS.parents[2]


def _compose() -> str:
    return (HAILPATH / "c-infra-kafka" / "docker-compose.yaml").read_text()


def _pin(pattern: str) -> str:
    match = re.search(pattern, _compose())
    assert match, pattern
    return match.group(1)


def test_kafka_readme_names_compose_image_pins():
    kafka = _pin(r"image:\s*\$\{KAFKA_IMAGE:-([^}]+)\}")
    sr = _pin(r"image:\s*\$\{SCHEMA_REGISTRY_IMAGE:-([^}]+)\}")
    ksql = _pin(r"image:\s*\$\{KSQLDB_IMAGE:-([^}]+)\}")
    readme = (HAILPATH / "c-infra-kafka" / "README.md").read_text()
    assert kafka in readme
    assert sr in readme
    assert ksql in readme
    assert "1280m" in readme and "768m" in readme
    assert "5.5g" in readme
    assert "PROXY_KSQLDB_PORT" in readme
    assert "no ZooKeeper" in readme or "There is no ZooKeeper" in readme
