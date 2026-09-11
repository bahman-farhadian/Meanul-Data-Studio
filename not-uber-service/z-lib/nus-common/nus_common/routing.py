"""Finding the best path over the real street network.

This is the one place in the stack that asks pgRouting a question, and it is
the most expensive query anywhere in the pipeline - roughly 50 to 150
milliseconds per call. It is shared: dispatch-service calls it once per live
trip, and h-bootstrap calls it once per historical trip while inventing a
seeded week, so a pickup and dropoff picked at random are never priced
without first checking they are actually connected by a real road.

The cost of a road segment is its travel time multiplied by how congested it
is at this time of day. That is what makes the answer change between rush
hour and three in the morning: the same two points, a different best path.
"""

from nus_common import config, postgres
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
         WHERE on_main_network
         ORDER BY the_geom <-> ST_SetSRID(ST_MakePoint(%(from_lon)s, %(from_lat)s), 4326)
         LIMIT 1
    ),
    end_vertex AS (
        SELECT id FROM ways_vertices_pgr
         WHERE on_main_network
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


NEAREST_ROAD_POINT_SQL = """
    SELECT ST_Y(the_geom) AS lat, ST_X(the_geom) AS lon,
           ST_Distance(the_geom::geography, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography) AS distance_m
      FROM ways_vertices_pgr
     WHERE on_main_network
     ORDER BY the_geom <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)
     LIMIT 1
"""


def nearest_road_point(lat: float, lon: float, max_snap_km: float | None = None) -> tuple[float, float] | None:
    """Snap to the nearest vertex on the verified, connected road network.

    None means nothing routable was within max_snap_km of (lat, lon) - the
    candidate point was in water, a park, or otherwise off the map. The
    caller re-picks rather than using a point that was never really there.

    Restricted to on_main_network (the dominant strongly-connected
    component, marked once when the graph is built - see
    h-bootstrap/lion-prepare/build-graph.sql), so this can never return a
    point on a real but disconnected island (Governors, Liberty, Ellis -
    none of them have a car bridge) or a boundary-clipping artifact.
    """
    limit_km = max_snap_km if max_snap_km is not None else config.number("MAX_SNAP_KM", 0.5)

    with postgres.read_connection() as conn:
        row = postgres.fetch_one(conn, NEAREST_ROAD_POINT_SQL, {"lat": lat, "lon": lon})

    if not row or row["distance_m"] / 1000.0 > limit_km:
        return None
    return float(row["lat"]), float(row["lon"])


def random_road_point_in_zone(grid, zone_id: str, rng, attempts: int = 5) -> tuple[float, float]:
    """A random point inside one zone, snapped to the real, connected road
    network - never open water, a park, or a real island with no car bridge.

    grid is anything with random_point_in(zone_id, rng) and centre_of(zone_id)
    - a nus_common.citygrid.CityGrid, from any caller (h-bootstrap,
    driver-service, passenger-service all draw the same grid from it).

    Retries with a fresh random point a few times before falling back to the
    zone's own centre (far more likely to be near real infrastructure than
    an arbitrary corner). If even that fails - a zone that is mostly water -
    the raw, unsnapped point is returned rather than blocking forever; it is
    a rare enough case not to be worth failing the whole run over.
    """
    for _ in range(attempts):
        lat, lon = grid.random_point_in(zone_id, rng)
        snapped = nearest_road_point(lat, lon)
        if snapped is not None:
            return snapped

    lat, lon = grid.centre_of(zone_id)
    snapped = nearest_road_point(lat, lon)
    if snapped is not None:
        return snapped

    log.warning("no road point found near zone, using an unsnapped point", extra={"zone_id": zone_id})
    return grid.random_point_in(zone_id, rng)


SERVICABLE_ZONE_IDS_SQL = "SELECT zone_id FROM city_zones WHERE servicable ORDER BY zone_id"


def servicable_zone_ids() -> list[str]:
    """Zone ids whose centroid actually reaches a real, connected road.

    CITY_MIN/MAX_LAT/LON is a rectangle; a real city's shape is not, so a
    rectangle drawn around one has corners that can sit mostly in open
    water, an airport, or another jurisdiction the imported map never
    covered at all - h-bootstrap marks those zones unservicable once,
    right after the street graph is restored (bootstrap/zones.py), and
    every caller here just reads the flag rather than re-discovering it
    one bad point at a time through the max_snap_km retry.
    """
    with postgres.read_connection() as conn:
        rows = postgres.fetch_all(conn, SERVICABLE_ZONE_IDS_SQL)
    return [row["zone_id"] for row in rows]
