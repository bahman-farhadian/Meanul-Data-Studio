"""Dividing the city into zones.

Zones are the unit every demand question is answered in: a hotspot score, a
surge multiplier, a dashboard row. They are built as a simple grid over the
city box rather than taken from a real neighbourhood map, for two reasons:
the grid needs no extra download, and every zone is the same size, which
makes "this zone is busier than that one" mean what it says.
"""

from nus_common import postgres
from nus_common.logging import get_logger

from bootstrap.settings import Settings

log = get_logger(__name__)


def zone_id(row: int, col: int) -> str:
    """The id of one grid cell, for example z-02-05."""
    return f"z-{row:02d}-{col:02d}"


def seed(settings: Settings) -> int:
    """Create the zone grid. Existing zones are left alone."""
    lat_step = (settings.max_lat - settings.min_lat) / settings.grid_rows
    lon_step = (settings.max_lon - settings.min_lon) / settings.grid_cols

    rows = []
    for row in range(settings.grid_rows):
        for col in range(settings.grid_cols):
            south = settings.min_lat + row * lat_step
            north = south + lat_step
            west = settings.min_lon + col * lon_step
            east = west + lon_step
            rows.append(
                {
                    "zone_id": zone_id(row, col),
                    "name": f"Zone {row + 1}-{col + 1}",
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
    return [
        zone_id(row, col)
        for row in range(settings.grid_rows)
        for col in range(settings.grid_cols)
    ]


def random_point_in_zone(settings: Settings, zid: str, rng) -> tuple[float, float]:
    """A random latitude and longitude inside one zone."""
    _, row_text, col_text = zid.split("-")
    row, col = int(row_text), int(col_text)
    lat_step = (settings.max_lat - settings.min_lat) / settings.grid_rows
    lon_step = (settings.max_lon - settings.min_lon) / settings.grid_cols
    south = settings.min_lat + row * lat_step
    west = settings.min_lon + col * lon_step
    return (
        rng.uniform(south, south + lat_step),
        rng.uniform(west, west + lon_step),
    )
