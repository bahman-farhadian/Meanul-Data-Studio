"""Finding the best path over the real street network.

This is the one place in the stack that asks pgRouting a question, and the
most expensive step anywhere in the pipeline - roughly 50 to 150 milliseconds
per trip. Everything else in dispatch is arithmetic.

The cost of a road segment is its travel time multiplied by how congested it
is at this time of day. That is what makes the answer change between rush
hour and three in the morning: the same two points, a different best path.
"""

from nus_common import postgres
from nus_common.logging import get_logger

log = get_logger(__name__)

# The four periods of the day. Listed here because the period is written
# into the edge query below as text, and a value that goes into a statement
# as text must come from a fixed list, never from anything a caller made up.
PERIODS = {"night", "morning", "afternoon", "evening"}

# The edge list pgRouting walks over. pgRouting takes this as a complete
# statement in a string, which is why the period is placed in it here rather
# than passed as a parameter.
#
# cost_s is the travel time of the segment in seconds, as computed by the map
# import. Multiplying it by congestion_factor is the whole traffic model: a
# segment that is twice as congested costs twice as much to drive.
EDGES_SQL_TEMPLATE = """
    SELECT w.gid AS id,
           w.source,
           w.target,
           w.cost_s * COALESCE(st.congestion_factor, 1.0) AS cost,
           w.reverse_cost_s * COALESCE(st.congestion_factor, 1.0) AS reverse_cost
      FROM ways w
      LEFT JOIN segment_traffic st
             ON st.way_id = w.gid AND st.period = '{period}'
"""

ROUTE_SQL = """
    WITH start_vertex AS (
        SELECT id FROM ways_vertices_pgr
         ORDER BY the_geom <-> ST_SetSRID(ST_MakePoint(%(from_lon)s, %(from_lat)s), 4326)
         LIMIT 1
    ),
    end_vertex AS (
        SELECT id FROM ways_vertices_pgr
         ORDER BY the_geom <-> ST_SetSRID(ST_MakePoint(%(to_lon)s, %(to_lat)s), 4326)
         LIMIT 1
    )
    SELECT COALESCE(SUM(w.length_m) / 1000.0, 0)                       AS route_km,
           COALESCE(MAX(d.agg_cost), 0)                                AS seconds,
           ST_AsText(ST_LineMerge(ST_Collect(w.the_geom ORDER BY d.seq))) AS route_wkt
      FROM pgr_dijkstra(
               %(edges_sql)s,
               (SELECT id FROM start_vertex),
               (SELECT id FROM end_vertex),
               directed => true
           ) d
      JOIN ways w ON w.gid = d.edge
     WHERE d.edge > 0
"""


def route(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float, period: str
) -> tuple[float, int, str | None] | None:
    """Return (kilometres, seconds, route as text) or None if there is no path.

    The nearest street corner to each point is used as the start and the end.
    Somebody standing in the middle of a park still gets a route: it begins
    at the nearest place a car can be.

    None means the two points are not connected in the imported map - usually
    a point outside the imported area. The caller treats that as "no driver
    found" rather than crashing.
    """
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}; expected one of {sorted(PERIODS)}")

    edges_sql = EDGES_SQL_TEMPLATE.format(period=period)

    with postgres.read_connection() as conn:
        row = postgres.fetch_one(
            conn,
            ROUTE_SQL,
            {
                "from_lat": from_lat, "from_lon": from_lon,
                "to_lat": to_lat, "to_lon": to_lon,
                "edges_sql": edges_sql,
            },
        )

    if not row or not row["route_km"]:
        return None

    seconds = int(row["seconds"]) if row["seconds"] else 0
    # A route that claims to take no time is a broken cost column, not a
    # teleport. Fall back to a plain speed estimate so the trip still works.
    if seconds <= 0:
        seconds = int(float(row["route_km"]) / 25.0 * 3600)

    return float(row["route_km"]), seconds, row["route_wkt"]
