"""Small geography and time-of-day helpers.

Kept here because several components need exactly the same answer to the
same question, and two different answers would quietly disagree in the data.
"""

import math
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EARTH_RADIUS_KM = 6371.0

# The day is split into four six-hour parts. Hotspot scores are kept per zone
# AND per part of the day, because "this zone is busy" is only true at
# certain hours. The names are used in Redis keys and in ClickHouse, so they
# must match the values listed in the city_hotspots Avro schema.
DAY_PERIODS = ("night", "morning", "afternoon", "evening")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two points, in kilometres.

    This is the distance a bird would fly, not the distance a car drives.
    Use it for "which driver is nearest" style questions; the real driving
    distance comes from pgRouting.
    """
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def point_at_fraction(path: list[tuple[float, float]], share: float) -> tuple[float, float]:
    """(lat, lon) a fraction of the way along a polyline, 0.0 = start, 1.0 = end.

    Distance-weighted, so a long avenue is not treated the same as a short
    cross-street. Used by dispatch to place the car on the real route
    instead of on the straight line between pickup and dropoff.
    """
    if not path:
        raise ValueError("empty path")
    share = max(0.0, min(float(share), 1.0))
    if len(path) == 1 or share == 0:
        return path[0]
    if share >= 1:
        return path[-1]

    cumulative = [0.0]
    for (lat1, lon1), (lat2, lon2) in zip(path, path[1:]):
        cumulative.append(cumulative[-1] + distance_km(lat1, lon1, lat2, lon2))
    total = cumulative[-1]
    if total == 0:
        return path[0]

    target = total * share
    i = 0
    while i < len(cumulative) - 2 and cumulative[i + 1] < target:
        i += 1
    seg_start, seg_end = cumulative[i], cumulative[i + 1]
    step = (target - seg_start) / (seg_end - seg_start) if seg_end > seg_start else 0.0
    lat1, lon1 = path[i]
    lat2, lon2 = path[i + 1]
    return lat1 + (lat2 - lat1) * step, lon1 + (lon2 - lon1) * step


def points_along_linestring(wkt: str, count: int) -> list[tuple[float, float]]:
    """N evenly-spaced (lat, lon) points along a route, start to end.

    `wkt` is the ST_AsText() of the LINESTRING nus_common.routing.route()
    returns - the real street path pgRouting found, not a straight line
    between its two endpoints. Used to plot position history that follows
    actual streets instead of cutting through whatever sits between pickup
    and dropoff.

    A LineMerge over a connected pgRouting path is normally a single
    LINESTRING, but is not guaranteed to be, so a MULTILINESTRING is also
    accepted: its parts are walked in the order pgRouting returned them,
    which is the order they appear in the WKT.
    """
    coords = _linestring_coords(wkt)
    if count <= 1 or len(coords) == 1:
        lat, lon = coords[0]
        return [(lat, lon)] * max(count, 1)

    cumulative = [0.0]
    for (lat1, lon1), (lat2, lon2) in zip(coords, coords[1:]):
        cumulative.append(cumulative[-1] + distance_km(lat1, lon1, lat2, lon2))
    total = cumulative[-1]

    if total == 0:
        lat, lon = coords[0]
        return [(lat, lon)] * count

    points = []
    for step in range(count):
        target = total * step / (count - 1)
        i = 0
        while i < len(cumulative) - 2 and cumulative[i + 1] < target:
            i += 1
        seg_start, seg_end = cumulative[i], cumulative[i + 1]
        share = (target - seg_start) / (seg_end - seg_start) if seg_end > seg_start else 0.0
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        points.append((lat1 + (lat2 - lat1) * share, lon1 + (lon2 - lon1) * share))
    return points


def linestring_vertices(wkt: str) -> list[tuple[float, float]]:
    """Ordered (lat, lon) vertices of a WKT (MULTI)LINESTRING."""
    return _linestring_coords(wkt)


def _linestring_coords(wkt: str) -> list[tuple[float, float]]:
    """Parse a WKT (MULTI)LINESTRING into a flat [(lat, lon), ...] list.

    WKT coordinates are written "lon lat" (x y); this returns (lat, lon), the
    order every other coordinate pair in this codebase uses.
    """
    body = wkt.strip()
    body = body[body.index("(") + 1 : body.rindex(")")]
    coords = []
    for part in body.replace("(", "").replace(")", "").split(","):
        lon_text, lat_text = part.strip().split()
        coords.append((float(lat_text), float(lon_text)))
    return coords


def sim_zoneinfo() -> ZoneInfo:
    """The timezone the demand curve is indexed in.

    Every stored timestamp is still UTC (TZ=UTC on the containers). This is
    only how "what hour is it for the generators" is answered. SIM_TIMEZONE
    comes from the process environment; America/New_York is the NYC default
    in .env.example. An unknown name falls back to UTC rather than crashing
    a tick loop.
    """
    name = os.environ.get("SIM_TIMEZONE") or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def in_sim_tz(moment: datetime) -> datetime:
    """The same instant, in SIM_TIMEZONE."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(sim_zoneinfo())


def day_period(moment: datetime) -> str:
    """Which six-hour part of the day a moment belongs to.

    Read in SIM_TIMEZONE so "morning" is New York morning when that is
    the configured zone, not UTC morning. Stored timestamps stay UTC.
    """
    hour = in_sim_tz(moment).hour
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def utc_now() -> datetime:
    """The current time, always with a timezone attached.

    Python's datetime.now() gives a value with no timezone, which compares
    badly with values that have one. This never returns that.
    """
    return datetime.now(tz=timezone.utc)


def to_millis(moment: datetime) -> int:
    """Turn a moment into the millisecond number Avro and ClickHouse expect."""
    return int(moment.timestamp() * 1000)
