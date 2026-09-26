"""A driver without a vehicle profile must not quietly become an economy car.

This is the defect that made fulfilment look like a supply problem for two
blocks. driver-service waited for the first `driver:*` key to appear, then
read the roster once and never again. The vehicle profiles arrive on a
different Debezium topic, so at that moment `vehicle:*` was still empty -
and a missing vehicle defaulted to "economy". The whole fleet landed in one
tier: measured on Dionysus, the per-tier GEO sets held 1,871 economy, zero
xl and zero premium, against a fleet h-bootstrap seeds 70/20/10.

Dispatch matches within the requested tier, so every xl and premium request
found no candidate and ended `no_driver_found` - which reads downstream as
thin supply and is nothing of the sort.
"""

from __future__ import annotations

import json
import random

from nus_common import redis_client

from driver_service.__main__ import cache_counts, load_roster, settled_caches


class FakeRedis:
    """Just enough Redis to exercise the roster load."""

    def __init__(self, data: dict[str, str]):
        self.data = dict(data)

    def scan_iter(self, match: str, count: int = 100):
        prefix = match.rstrip("*")
        return iter([k for k in sorted(self.data) if k.startswith(prefix)])

    def get(self, key: str):
        return self.data.get(key)


class Grid:
    def zone_of(self, lat, lon):
        return "142"


def _driver(number: int, tier: str | None) -> dict[str, str]:
    driver_id = f"drv-{number:07d}"
    rows = {
        redis_client.driver_key(driver_id): json.dumps({
            "driver_id": driver_id, "home_zone_id": "142",
            "last_lat": 40.75, "last_lon": -73.98,
        })
    }
    if tier is not None:
        rows[redis_client.vehicle_key(driver_id)] = json.dumps({
            "driver_id": driver_id, "vehicle_type": tier,
        })
    return rows


def test_a_missing_vehicle_is_counted_not_swallowed() -> None:
    data: dict[str, str] = {}
    for n in range(1, 4):
        data.update(_driver(n, None))
    redis = FakeRedis(data)

    drivers, missing = load_roster(redis, Grid(), random.Random(1), ["142"])
    assert len(drivers) == 3
    assert missing == 3, "a driver with no vehicle profile was reported as fine"


def test_the_tier_is_read_when_the_profile_is_there() -> None:
    data: dict[str, str] = {}
    for n, tier in enumerate(["economy", "xl", "premium"], start=1):
        data.update(_driver(n, tier))
    redis = FakeRedis(data)

    drivers, missing = load_roster(redis, Grid(), random.Random(1), ["142"])
    assert missing == 0
    assert sorted(d.vehicle_type for d in drivers.values()) == [
        "economy", "premium", "xl"
    ]


def test_the_gate_refuses_a_half_filled_cache() -> None:
    """Drivers present, vehicles not: exactly the state that caused this."""
    data: dict[str, str] = {}
    for n in range(1, 5):
        data.update(_driver(n, None))
    redis = FakeRedis(data)

    assert cache_counts(redis) == (4, 0)
    check = settled_caches(redis)
    assert check() is False
    assert check() is False, "a steady but empty vehicle cache was accepted"


def test_the_gate_refuses_a_cache_that_is_still_growing() -> None:
    """Equal counts mid-fill is not the same as both being done.

    The two topics fill at the same time, so vehicles can draw level with
    drivers while both are still arriving. Requiring the driver count to
    hold still between two polls is what tells those apart.
    """
    redis = FakeRedis(_driver(1, "economy"))
    check = settled_caches(redis)
    assert check() is False

    redis.data.update(_driver(2, "xl"))
    assert check() is False, "the cache grew between polls and was still accepted"

    assert check() is True, "a settled, complete cache was not accepted"


def test_the_gate_accepts_a_complete_settled_cache() -> None:
    data: dict[str, str] = {}
    for n, tier in enumerate(["economy", "xl", "premium", "economy"], start=1):
        data.update(_driver(n, tier))
    redis = FakeRedis(data)

    check = settled_caches(redis)
    assert check() is False, "the first poll has nothing to compare against"
    assert check() is True
