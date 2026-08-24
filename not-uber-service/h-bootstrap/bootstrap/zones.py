"""Creating the city zones in the database.

The grid arithmetic itself lives in nus_common.citygrid, so that this
component and the six services all draw exactly the same city. This module
only writes the result into PostgreSQL.
"""

from nus_common import postgres
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
    """Create the zone grid. Existing zones are left alone."""
    grid = grid_from(settings)

    rows = []
    for zone_id in grid.all_zone_ids():
        south, west, north, east = grid.bounds_of(zone_id)
        _, row_text, col_text = zone_id.split("-")
        rows.append(
            {
                "zone_id": zone_id,
                "name": f"Zone {int(row_text) + 1}-{int(col_text) + 1}",
                "west": west, "south": south, "east": east, "north": north,
            }
        )

    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO city_zones (zone_id, name, boundary, centroid)
                VALUES (
                    %(zone_id)s,
                    %(name)s,
                    -- A rectangle built from the four corners of the cell.
                    ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326),
                    ST_Centroid(ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326))
                )
                ON CONFLICT (zone_id) DO NOTHING
                """,
                rows,
            )
        conn.commit()

    log.info("zones ready", extra={"zones": len(rows)})
    return len(rows)


def all_zone_ids(settings: Settings) -> list[str]:
    """Every zone id, without going back to the database."""
    return grid_from(settings).all_zone_ids()


def random_point_in_zone(settings: Settings, zone_id: str, rng) -> tuple[float, float]:
    """A random latitude and longitude inside one zone."""
    return grid_from(settings).random_point_in(zone_id, rng)
