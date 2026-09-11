"""Creating the city zones in the database, and sharing one loaded grid.

NYC TLC's real Taxi Zones (see h-bootstrap/lion-prepare/taxi-zones.sql) are
restored once, per bring-up, into city_zones_source alongside the routable
graph. seed() copies that into city_zones, computing which zones are
servicable against the just-restored street graph - the shared CityGrid
(nus_common.citygrid) then loads only the servicable set from there.

grid() caches that load for the life of this process: CityGrid.load() is a
real database round trip plus parsing 263 real polygons, and the functions
below are called once per driver, per passenger, per historical trip - up
to hundreds of thousands of times in one bootstrap run. Loading it once and
reusing it is what keeps this the same cheap, in-memory lookup it always
was, now backed by real geometry instead of grid arithmetic.
"""

from nus_common import postgres, routing
from nus_common.citygrid import CityGrid
from nus_common.logging import get_logger

log = get_logger(__name__)

ZONE_SOURCE_SQL = """
    SELECT zone_id, zone_name, borough,
           ST_Y(ST_Centroid(boundary)) AS centre_lat,
           ST_X(ST_Centroid(boundary)) AS centre_lon
      FROM city_zones_source
"""

INSERT_ZONES_SQL = """
    INSERT INTO city_zones (zone_id, name, borough, boundary, centroid, servicable)
    SELECT zone_id, zone_name, borough, boundary, ST_Centroid(boundary), true
      FROM city_zones_source
    ON CONFLICT (zone_id) DO NOTHING
"""

_grid: CityGrid | None = None


def grid() -> CityGrid:
    """The shared city grid, loaded once per process and reused."""
    global _grid
    if _grid is None:
        _grid = CityGrid.load()
    return _grid


def seed() -> int:
    """Copy NYC TLC's real zones from the restored source table. Existing zones are left alone.

    Each zone's servicability is checked here, against the just-restored
    street graph, and baked into city_zones once - see routing.py's
    servicable_zone_ids() for why. Deliberately does not use grid()/
    CityGrid.load(): this is the function that populates city_zones in the
    first place, so nothing can load it from there yet.
    """
    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(INSERT_ZONES_SQL)
        conn.commit()

    with postgres.read_connection() as conn:
        rows = postgres.fetch_all(conn, ZONE_SOURCE_SQL)

    unservicable_ids = [
        row["zone_id"]
        for row in rows
        if routing.nearest_road_point(float(row["centre_lat"]), float(row["centre_lon"])) is None
    ]

    if unservicable_ids:
        with postgres.write_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE city_zones SET servicable = false WHERE zone_id = ANY(%s)",
                    (unservicable_ids,),
                )
            conn.commit()

    log.info("zones ready", extra={"zones": len(rows), "unservicable": len(unservicable_ids)})
    if unservicable_ids:
        log.warning(
            "some zones cannot reach a real road from their own centroid - "
            "excluded from demand generation and home-zone assignment",
            extra={"unservicable": len(unservicable_ids), "total": len(rows)},
        )
    return len(rows)


def all_zone_ids() -> list[str]:
    """Every servicable zone id, without going back to the database."""
    return grid().all_zone_ids()


def random_point_in_zone(zone_id: str, rng) -> tuple[float, float]:
    """A random latitude and longitude inside one zone's real polygon.

    Raw polygon arithmetic, no road awareness - use
    random_road_point_in_zone for anything that becomes a pickup, dropoff,
    or driver location.
    """
    return grid().random_point_in(zone_id, rng)


def random_road_point_in_zone(zone_id: str, rng, attempts: int = 5) -> tuple[float, float]:
    """A random point inside one zone, snapped to the real, connected road
    network. See nus_common.routing.random_road_point_in_zone - this is
    just that, with bootstrap's own shared grid.
    """
    return routing.random_road_point_in_zone(grid(), zone_id, rng, attempts)
