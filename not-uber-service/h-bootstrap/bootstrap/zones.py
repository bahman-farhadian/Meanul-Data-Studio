"""Creating the city zones in the database.

The grid arithmetic itself lives in nus_common.citygrid, so that this
component and the six services all draw exactly the same city. This module
only writes the result into PostgreSQL.
"""

from nus_common import postgres, routing
from nus_common.citygrid import CityGrid
from nus_common.logging import get_logger

from bootstrap.settings import Settings

log = get_logger(__name__)


def grid_from(settings: Settings) -> CityGrid:
    """Build the grid from bootstrap's own settings."""
    return CityGrid(
        min_lat=settings.min_lat,
        max_lat=settings.max_lat,
        min_lon=settings.min_lon,
        max_lon=settings.max_lon,
        rows=settings.grid_rows,
        cols=settings.grid_cols,
    )


def seed(settings: Settings) -> int:
    """Create the zone grid. Existing zones are left alone.

    Each zone's servicability is checked here, against the just-restored
    street graph, and baked into city_zones once - see routing.py's
    servicable_zone_ids() for why.
    """
    grid = grid_from(settings)

    rows = []
    unservicable = 0
    for zone_id in grid.all_zone_ids():
        south, west, north, east = grid.bounds_of(zone_id)
        _, row_text, col_text = zone_id.split("-")
        centre_lat, centre_lon = grid.centre_of(zone_id)
        servicable = routing.nearest_road_point(centre_lat, centre_lon) is not None
        if not servicable:
            unservicable += 1
        rows.append(
            {
                "zone_id": zone_id,
                "name": f"Zone {int(row_text) + 1}-{int(col_text) + 1}",
                "west": west, "south": south, "east": east, "north": north,
                "servicable": servicable,
            }
        )

    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO city_zones (zone_id, name, boundary, centroid, servicable)
                VALUES (
                    %(zone_id)s,
                    %(name)s,
                    -- A rectangle built from the four corners of the cell.
                    ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326),
                    ST_Centroid(ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326)),
                    %(servicable)s
                )
                ON CONFLICT (zone_id) DO NOTHING
                """,
                rows,
            )
        conn.commit()

    log.info("zones ready", extra={"zones": len(rows), "unservicable": unservicable})
    if unservicable:
        log.warning(
            "some zones cannot reach a real road from their own centroid - "
            "excluded from demand generation and home-zone assignment",
            extra={"unservicable": unservicable, "total": len(rows)},
        )
    return len(rows)


def all_zone_ids(settings: Settings) -> list[str]:
    """Every zone id, without going back to the database."""
    return grid_from(settings).all_zone_ids()


def random_point_in_zone(settings: Settings, zone_id: str, rng) -> tuple[float, float]:
    """A random latitude and longitude inside one zone.

    Raw rectangle arithmetic, no road awareness - use
    random_road_point_in_zone for anything that becomes a pickup, dropoff,
    or driver location.
    """
    return grid_from(settings).random_point_in(zone_id, rng)


def random_road_point_in_zone(
    settings: Settings, zone_id: str, rng, attempts: int = 5,
) -> tuple[float, float]:
    """A random point inside one zone, snapped to the real, connected road
    network. See nus_common.routing.random_road_point_in_zone - this is
    just that, with bootstrap's own grid.
    """
    return routing.random_road_point_in_zone(grid_from(settings), zone_id, rng, attempts)
