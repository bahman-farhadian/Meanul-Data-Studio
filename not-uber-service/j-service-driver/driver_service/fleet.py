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
    vehicle_type: str = "economy"
    status: str = OFFLINE
    trip_id: str | None = None
    # Where this driver is currently heading, if anywhere.
    target_lat: float | None = None
    target_lon: float | None = None
    speed_kmh: float = 0.0
    heading_deg: float = 0.0
    # Street-following polyline. Empty means sit still. A single point is
    # a hop to that coordinate (used only if pgRouting found no path).
    path: list[tuple[float, float]] = field(default_factory=list)
    path_i: int = 0
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

    def follow(self, path: list[tuple[float, float]]) -> None:
        """Walk this polyline on subsequent ticks. Last point is the target.

        Start at the nearest vertex to where the car already is, so a
        re-follow (matched then accepted then en_route_pickup) does not
        send the driver back to the origin of the line.
        """
        if not path:
            return
        self.path = list(path)
        self.target_lat, self.target_lon = self.path[-1]
        best_i, best_d = 0, _distance_km(self.lat, self.lon, path[0][0], path[0][1])
        for i, (lat, lon) in enumerate(path):
            dist = _distance_km(self.lat, self.lon, lat, lon)
            if dist < best_d:
                best_i, best_d = i, dist
        self.path_i = best_i

    def head_towards(self, lat: float, lon: float) -> None:
        self.follow([(lat, lon)])

    def arrived(self, tolerance_km: float = 0.15) -> bool:
        """True when the driver is close enough to the target to call it done."""
        if self.path and self.path_i < len(self.path):
            return False
        if self.target_lat is None or self.target_lon is None:
            return True
        return _distance_km(self.lat, self.lon, self.target_lat, self.target_lon) <= tolerance_km

    def move(self, seconds: float, speed_kmh: float, rng: random.Random) -> None:
        """Move for one tick along the current street path.

        Live positions used to lerp the two endpoints (Grafana showed the
        trail cutting across blocks). The path is the densified pgRouting
        geometry; this only walks it.
        """
        if not self.online or self.target_lat is None or self.target_lon is None:
            self.speed_kmh = 0.0
            return

        actual_speed = speed_kmh * rng.uniform(0.7, 1.3)
        remaining = actual_speed * seconds / 3600.0
        waypoints = self.path if self.path else [(self.target_lat, self.target_lon)]

        while remaining > 0 and self.path_i < len(waypoints):
            tlat, tlon = waypoints[self.path_i]
            dist = _distance_km(self.lat, self.lon, tlat, tlon)
            if dist < 1e-6:
                self.path_i += 1
                continue
            self.heading_deg = (math.degrees(math.atan2(tlon - self.lon, tlat - self.lat)) + 360) % 360
            if dist <= remaining:
                self.lat, self.lon = tlat, tlon
                remaining -= dist
                self.path_i += 1
            else:
                share = remaining / dist
                self.lat += (tlat - self.lat) * share
                self.lon += (tlon - self.lon) * share
                remaining = 0

        if self.path_i >= len(waypoints):
            self.lat, self.lon = waypoints[-1]
            self.path = []
            self.path_i = 0

        self.speed_kmh = actual_speed
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
