"""Demand supply is last-per-driver, not the GPS firehose."""

from city_service.counters import ZoneCounter
from city_service.supply import (
    LAST_KNOWN_SPEEDS_SQL,
    apply_last_known_speeds,
    refresh_free_from_geo,
)
from hailpath_common import redis_client


class _Grid:
    def zone_of(self, lat: float, lon: float) -> int:
        if lon < -74.0:
            return 1
        return 2


class _Redis:
    def __init__(self, geo: dict[str, list[tuple[str, float, float]]]):
        self._geo = geo

    def zrange(self, key, start, end):
        return [member for member, _lon, _lat in self._geo.get(key, [])]

    def geopos(self, key, *members):
        by_id = {member: (lon, lat) for member, lon, lat in self._geo.get(key, [])}
        return [by_id.get(member) for member in members]


def test_free_drivers_come_from_geo_last_position():
    economy = redis_client.geo_available_drivers_key("economy")
    xl = redis_client.geo_available_drivers_key("xl")
    redis = _Redis({
        economy: [("10", -74.1, 40.7), ("11", -73.9, 40.7)],
        xl: [("20", -74.1, 40.71)],
    })
    zones = {1: ZoneCounter(), 2: ZoneCounter()}
    refresh_free_from_geo(zones, _Grid(), redis)
    assert zones[1].free_drivers == {10, 20}
    assert zones[2].free_drivers == {11}


def test_a_driver_who_left_geo_is_no_longer_free():
    economy = redis_client.geo_available_drivers_key("economy")
    zones = {1: ZoneCounter()}
    zones[1].free_drivers.add(99)
    redis = _Redis({economy: [("10", -74.1, 40.7)]})
    refresh_free_from_geo(zones, _Grid(), redis)
    assert 99 not in zones[1].free_drivers
    assert zones[1].free_drivers == {10}


def test_speeds_come_from_last_known_rows():
    zones = {1: ZoneCounter(), 2: ZoneCounter()}
    apply_last_known_speeds(zones, [(1, 20.0), (1, 30.0), (2, 0.5)])
    assert zones[1].speed_samples == 2
    assert zones[1].average_speed_kmh == 25.0
    assert zones[2].speed_samples == 0


def test_last_known_sql_does_not_scan_the_gps_table():
    assert "driver_last_known" in LAST_KNOWN_SPEEDS_SQL
    assert "FINAL" in LAST_KNOWN_SPEEDS_SQL
    assert "driver_positions" not in LAST_KNOWN_SPEEDS_SQL
    assert "driver_location" not in LAST_KNOWN_SPEEDS_SQL
