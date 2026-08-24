"""Small geography and time-of-day helpers.

Kept here because several components need exactly the same answer to the
same question, and two different answers would quietly disagree in the data.
"""

import math
from datetime import datetime, timezone

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


def day_period(moment: datetime) -> str:
    """Which six-hour part of the day a moment belongs to.

    The moment is read in UTC, like every timestamp in the stack.
    """
    hour = moment.astimezone(timezone.utc).hour
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
