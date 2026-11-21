"""Last-per-driver supply for demand scores and traffic.

City used to ingest every `driver_location` tick. That is the GPS firehose.
Idle positions already live in Redis GEO (`geo:drivers:available:*`); on-trip
speed already lives in `hailpath.driver_last_known`. Both are one row per
driver.
"""

from hailpath_common import redis_client

from city_service.counters import ZoneCounter

LAST_KNOWN_SPEEDS_SQL = """
SELECT zone_id, speed_kmh
FROM hailpath.driver_last_known FINAL
WHERE status = 'on_trip' AND speed_kmh > 1
"""


def refresh_free_from_geo(
    zones: dict[int, ZoneCounter], grid, redis,
) -> None:
    """Rebuild free_drivers from the last idle GEO position of each driver."""
    for counter in zones.values():
        counter.free_drivers.clear()
    for vehicle_type in redis_client.VEHICLE_TYPES:
        key = redis_client.geo_available_drivers_key(vehicle_type)
        members = redis.zrange(key, 0, -1)
        if not members:
            continue
        positions = redis.geopos(key, *members)
        for driver_id, pos in zip(members, positions):
            if not pos:
                continue
            lon, lat = float(pos[0]), float(pos[1])
            zone_id = grid.zone_of(lat, lon)
            zones[int(zone_id)].free_drivers.add(int(driver_id))


def apply_last_known_speeds(
    zones: dict[int, ZoneCounter], rows: list[tuple],
) -> None:
    """Rebuild speed samples from last-known on-trip rows, not the GPS stream."""
    for counter in zones.values():
        counter.reset_speeds()
    for zone_id, speed_kmh in rows:
        if zone_id is None or speed_kmh is None:
            continue
        zones[int(zone_id)].note_speed(float(speed_kmh))
