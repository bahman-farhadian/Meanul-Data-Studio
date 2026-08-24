"""The drivers themselves: where they are and what they are doing.

One object per simulated driver, all held in memory in one process. A driver
is small - a position, a status, somewhere it is heading - so a thousand of
them cost very little, and keeping them in one place means the whole fleet
can be moved one tick at a time.

Nothing here talks to Kafka, Redis or the database. That belongs to
__main__.py, so this file stays about how a driver behaves.
"""

import math
import random
from dataclasses import dataclass, field

# Roughly how many kilometres one degree of latitude is worth. Longitude
# shrinks towards the poles, which is why it is computed from the latitude.
KM_PER_LAT_DEGREE = 111.0

# The four states a driver can be in. They are the same words the Avro schema
# and the database use, so nothing has to be translated on the way out.
OFFLINE = "offline"
IDLE = "idle"
EN_ROUTE_PICKUP = "en_route_pickup"
ON_TRIP = "on_trip"


@dataclass
class Driver:
    driver_id: str
    lat: float
    lon: float
    home_zone_id: str
    status: str = OFFLINE
    trip_id: str | None = None
    # Where this driver is currently heading, if anywhere.
    target_lat: float | None = None
    target_lon: float | None = None
    speed_kmh: float = 0.0
    heading_deg: float = 0.0
    # True when something changed that the database has not been told yet.
    dirty: bool = field(default=False)

    @property
    def online(self) -> bool:
        return self.status != OFFLINE

    @property
    def free(self) -> bool:
        """Free means online and not working on a trip."""
        return self.status == IDLE

    def set_status(self, status: str, trip_id: str | None = None) -> None:
        if self.status != status or self.trip_id != trip_id:
            self.status = status
            self.trip_id = trip_id
            self.dirty = True

    def head_towards(self, lat: float, lon: float) -> None:
        self.target_lat, self.target_lon = lat, lon

    def arrived(self, tolerance_km: float = 0.15) -> bool:
        """True when the driver is close enough to the target to call it done."""
        if self.target_lat is None or self.target_lon is None:
            return True
        return _distance_km(self.lat, self.lon, self.target_lat, self.target_lon) <= tolerance_km

    def move(self, seconds: float, speed_kmh: float, rng: random.Random) -> None:
        """Move for one tick towards the target.

        The movement is a straight line, not a route along streets. A driver
        reporting its position does not know about the road network; the real
        route belongs to the trip, and dispatch-service computes that one with
        pgRouting.
        """
        if not self.online or self.target_lat is None or self.target_lon is None:
            self.speed_kmh = 0.0
            return

        # A little variation, so a hundred drivers do not move in lockstep.
        actual_speed = speed_kmh * rng.uniform(0.7, 1.3)
        travel_km = actual_speed * seconds / 3600.0

        delta_lat = self.target_lat - self.lat
        delta_lon = self.target_lon - self.lon
        km_per_lon_degree = KM_PER_LAT_DEGREE * max(math.cos(math.radians(self.lat)), 0.01)

        distance_km = math.hypot(delta_lat * KM_PER_LAT_DEGREE, delta_lon * km_per_lon_degree)
        if distance_km <= travel_km or distance_km == 0:
            # Close enough to land on it this tick.
            self.lat, self.lon = self.target_lat, self.target_lon
            self.speed_kmh = actual_speed
            return

        share = travel_km / distance_km
        self.lat += delta_lat * share
        self.lon += delta_lon * share
        self.speed_kmh = actual_speed
        # Compass bearing, 0 at north, going clockwise.
        self.heading_deg = (math.degrees(math.atan2(delta_lon, delta_lat)) + 360) % 360
        self.dirty = True


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth distance, good enough inside one city."""
    km_per_lon_degree = KM_PER_LAT_DEGREE * max(math.cos(math.radians(lat1)), 0.01)
    return math.hypot((lat2 - lat1) * KM_PER_LAT_DEGREE, (lon2 - lon1) * km_per_lon_degree)


def pick_target_zone(zone_scores: dict[str, float], zone_ids: list[str], rng: random.Random) -> str:
    """Choose where an idle driver should drift towards.

    Busy zones pull harder, which is the whole point of publishing hotspot
    scores: drivers move towards demand instead of wandering. The +0.1 keeps
    every zone slightly possible, so the quiet ones do not empty completely.
    """
    weights = [zone_scores.get(zone_id, 0.0) + 0.1 for zone_id in zone_ids]
    return rng.choices(zone_ids, weights=weights, k=1)[0]
