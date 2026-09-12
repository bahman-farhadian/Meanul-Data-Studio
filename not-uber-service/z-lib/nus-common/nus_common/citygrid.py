"""The city's real zones, in one place.

These are NYC TLC's own official Taxi Zones - the same 263 neighborhood-
shaped polygons Uber, Lyft and yellow cabs actually report trips against -
not a synthetic grid. h-bootstrap/lion-prepare builds them from TLC's own
data once, on the host; bootstrap/zones.py copies them into city_zones,
computing which ones are servicable (their own centroid can reach a real
road). Every component that needs "which zone is this point in," "the
middle of a zone," or "a random point inside one" loads the same servicable
set from there and asks this class - two independent implementations of
the same lookup would quietly disagree.
"""

import math
from dataclasses import dataclass, field

import shapely
from shapely import STRtree
from shapely.geometry import Point

from nus_common import postgres
from nus_common.geo import distance_km

ZONES_SQL = """
    SELECT zone_id, name, borough,
           ST_AsText(boundary) AS boundary_wkt,
           ST_Y(centroid) AS lat, ST_X(centroid) AS lon
      FROM city_zones
     WHERE servicable
     ORDER BY zone_id
"""


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    borough: str
    boundary: object  # a shapely Polygon or MultiPolygon
    lat: float
    lon: float


@dataclass(frozen=True)
class CityGrid:
    zones: dict[str, Zone]
    index: STRtree = field(repr=False)
    index_zone_ids: list[str] = field(repr=False)

    @classmethod
    def load(cls) -> "CityGrid":
        """Load every servicable zone's real polygon, once.

        A single query, cached for the life of the process - every method
        below is then pure, synchronous, in-memory geometry, the same as
        when this was grid arithmetic. Reused across every caller
        (h-bootstrap, driver-service, passenger-service, dispatch-service,
        city-service all load exactly the same set).
        """
        with postgres.read_connection() as conn:
            rows = postgres.fetch_all(conn, ZONES_SQL)

        zones: dict[str, Zone] = {}
        for row in rows:
            zones[row["zone_id"]] = Zone(
                zone_id=row["zone_id"],
                name=row["name"],
                borough=row["borough"],
                boundary=shapely.from_wkt(row["boundary_wkt"]),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
            )

        index_zone_ids = list(zones.keys())
        index = STRtree([zones[zid].boundary for zid in index_zone_ids])
        return cls(zones=zones, index=index, index_zone_ids=index_zone_ids)

    def all_zone_ids(self) -> list[str]:
        return list(self.zones.keys())

    def zone_of(self, lat: float, lon: float) -> str:
        """Which zone a point falls in.

        A point outside every zone (open water, the harbor) is pulled to
        the nearest zone by centroid distance rather than refused: a
        driver who wandered off the real streets should still be counted
        somewhere. The STRtree query narrows candidates to the same
        handful the point's bounding box could plausibly be inside, so the
        common case (a point genuinely inside one zone) never scans all 263.
        """
        point = Point(lon, lat)
        for idx in self.index.query(point):
            zone_id = self.index_zone_ids[idx]
            if self.zones[zone_id].boundary.contains(point):
                return zone_id
        return min(
            self.zones,
            key=lambda zid: distance_km(lat, lon, self.zones[zid].lat, self.zones[zid].lon),
        )

    def bounds_of(self, zone_id: str) -> tuple[float, float, float, float]:
        """The (south, west, north, east) edges of one zone's bounding box."""
        west, south, east, north = self.zones[zone_id].boundary.bounds
        return south, west, north, east

    def centre_of(self, zone_id: str) -> tuple[float, float]:
        """The middle of one zone - its real centroid, not a bounding-box average."""
        zone = self.zones[zone_id]
        return zone.lat, zone.lon

    def random_point_in(self, zone_id: str, rng) -> tuple[float, float]:
        """A random point inside one zone's real polygon.

        Rejection sampling in the polygon's own bounding box: real NYC
        zone shapes are compact, not pathologically thin, so this
        converges in a handful of tries even for a multi-part zone like
        EWR. Falls back to the centroid in the practically-impossible case
        every attempt misses.
        """
        polygon = self.zones[zone_id].boundary
        west, south, east, north = polygon.bounds
        for _ in range(50):
            lat = rng.uniform(south, north)
            lon = rng.uniform(west, east)
            if polygon.contains(Point(lon, lat)):
                return lat, lon
        return self.centre_of(zone_id)

    def distance_decay_weights(
        self, from_zone_id: str, zone_ids: list[str], base_weights: list[float], decay_km: float = 5.0,
    ) -> list[float]:
        """Fold "how far from from_zone_id" into a set of zone popularity weights.

        Real rideshare demand is mostly short hops with a long tail, not a
        flat distribution across the whole city - picking a dropoff zone
        from popularity alone, independent of the pickup zone, produces
        trips of a near-identical average length regardless of where they
        started. decay_km is roughly the falloff scale: a zone that far from
        the pickup keeps about a third of its popularity weight, one twice as
        far keeps about a tenth.
        """
        from_lat, from_lon = self.centre_of(from_zone_id)
        weights = []
        for zone_id, base in zip(zone_ids, base_weights):
            lat, lon = self.centre_of(zone_id)
            distance = distance_km(from_lat, from_lon, lat, lon)
            weights.append(base * math.exp(-distance / decay_km))
        return weights
