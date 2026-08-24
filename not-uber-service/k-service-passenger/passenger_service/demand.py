"""How many people want a ride, and from where.

Demand is not flat. It has two peaks a day, and some parts of the city are
simply more popular than others. Both facts live here, because both are the
difference between a simulation and a random number generator.
"""

import hashlib
import random

# How busy each hour is, relative to the others. Two peaks - people going to
# work and people going home - and a smaller late-evening bump. The same
# shape h-bootstrap used for the seeded week, so live traffic continues the
# history rather than contradicting it.
HOUR_WEIGHTS = [
    0.3, 0.2, 0.15, 0.15, 0.2, 0.4,   # 00-05 night
    0.9, 1.6, 2.0, 1.4, 0.9, 0.9,     # 06-11 morning peak
    1.0, 1.0, 0.9, 1.0, 1.4, 2.0,     # 12-17 afternoon into evening peak
    1.7, 1.2, 1.0, 0.9, 0.8, 0.5,     # 18-23 evening
]


def hour_weight(hour: int) -> float:
    """How busy this hour of the day is. 1.0 is an ordinary hour."""
    return HOUR_WEIGHTS[hour % 24]


def zone_popularity(zone_id: str) -> float:
    """How popular a zone is, as a number between roughly 0.4 and 2.5.

    Derived from the zone's own name rather than drawn at random, so every
    service and every restart agrees about which zones are busy. A city
    where the popular areas moved every time a container restarted would
    make hotspots meaningless.
    """
    digest = hashlib.sha256(zone_id.encode()).digest()
    # The first byte gives a stable number from 0 to 255.
    return 0.4 + (digest[0] / 255.0) * 2.1


def requests_this_tick(
    base_per_minute: float, hour: int, tick_seconds: float, rng: random.Random
) -> int:
    """How many new ride requests to create in this tick.

    The expected number is the base rate adjusted for the time of day. The
    real number is drawn around it, so requests arrive unevenly the way they
    do in life instead of exactly four every minute.
    """
    expected = base_per_minute * hour_weight(hour) * (tick_seconds / 60.0)
    # A simple approximation of arrivals: the whole part, plus a chance at
    # one more for the fraction left over.
    whole = int(expected)
    return whole + (1 if rng.random() < (expected - whole) else 0)
