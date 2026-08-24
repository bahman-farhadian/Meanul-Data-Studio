"""What a trip costs.

    fare = (base + per_km x kilometres + per_minute x minutes) x surge

The estimate is worked out at assignment, from the predicted duration. The
final fare is worked out at the end, from the real duration - so a trip that
ran into traffic costs more than it was quoted, and one that flew through
costs less.

The surge multiplier comes from how busy the pickup zone is, which closes
the loop: city-service scores demand, the score raises the price, and the
price is stored on the trip so it can be explained afterwards.
"""

import json

from nus_common import redis_client
from nus_common.logging import get_logger

log = get_logger(__name__)


def surge_for(redis, zone_id: str, period: str) -> float:
    """How much to multiply the price by in this zone right now.

    1.0 when nothing is known. A missing hotspot score should mean ordinary
    prices, never a guess that charges somebody more.
    """
    raw = redis.get(redis_client.hotspot_key(zone_id, period))
    if not raw:
        return 1.0
    try:
        hotspot = json.loads(raw)
    except json.JSONDecodeError:
        return 1.0

    if "surge_multiplier" in hotspot:
        return float(hotspot["surge_multiplier"])

    # Older entries only carry the score, so derive the multiplier the same
    # way city-service does.
    return from_score(float(hotspot.get("demand_score", 0.0)))


def from_score(score: float) -> float:
    """Turn a demand score into a price multiplier.

    Stays at 1.0 until demand is clearly above normal, then rises gently and
    stops at 2.5. A multiplier that keeps climbing is a bug, not a feature.
    """
    if score <= 0.6:
        return 1.0
    return round(min(1.0 + (score - 0.6) * 1.6, 2.5), 2)


def fare(
    base: float, per_km: float, per_minute: float,
    route_km: float, seconds: int, surge: float,
) -> float:
    """The price of a trip, rounded to whole cents."""
    return round((base + per_km * route_km + per_minute * seconds / 60.0) * surge, 2)
