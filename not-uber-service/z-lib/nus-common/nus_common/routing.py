"""Finding a real path over the real street network.

This is the one place in the stack that asks pgRouting a question, and it is
the most expensive query anywhere in the pipeline. A single pgr_dijkstra call
ran roughly 50-150ms.

K_ROUTES was briefly raised above 1 so a repeated OD pair would not always
take the literal same streets - real route diversity. That was reverted:
pgr_ksp's own K-shortest-paths search (Yen's algorithm, via the Boost Graph
Library) is a documented, severe memory problem for K>1 on a real, large
graph - https://github.com/pgRouting/pgrouting/issues/1319 reports k=1 at
~100MB/1s versus k=2+ exploding past 100GB, on a graph of comparable size to
this project's own (270k roads/200k vertices there, 172k/110k here) - and
that is exactly what a real run here hit: a backend OOM-killed at ~15GB,
confirmed via pg_log_backend_memory_contexts to be memory entirely outside
Postgres's own tracked allocator, i.e. inside pgRouting's own C++ internals,
not anything work_mem/shared_buffers could bound. It is shared: dispatch-
service calls it once per live trip, and h-bootstrap calls it once per
historical trip while inventing a seeded week, so a pickup and dropoff
picked at random are never priced without first checking they are actually
connected by a real road.

The cost of a road segment is its travel time multiplied by how congested it
is at this time of day. That is what makes the answer change between rush
hour and three in the morning: the same two points, a different best path.
A repeated OD pair at the same hour does take the same literal streets again
- the honest tradeoff of reverting K_ROUTES to 1.
"""

import hashlib
import random
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg

from nus_common import config, postgres
from nus_common.logging import get_logger

log = get_logger(__name__)

_road_point_pools: dict[str, list[tuple[float, float]]] = {}

# The four periods of the day. Listed here because the period is written
# into the edge query below as text, and a value that goes into a statement
# as text must come from a fixed list, never from anything a caller made up.
PERIODS = {"night", "morning", "afternoon", "evening"}

# How many distinct candidate routes pgr_ksp considers per trip. Fixed at 1
# on purpose - see this module's own docstring: pgr_ksp for K>1 is a
# documented, severe memory problem (pgRouting issue #1319), confirmed to be
# the real cause of a backend OOM-kill on this project's own graph. K=1 is
# functionally pgr_dijkstra through the same call, not K-shortest-paths -
# the safe case that same issue confirms stays around 100MB/1s.
K_ROUTES = 1

# How the one actually driven is picked among the K candidates. At K_ROUTES=1
# this trivially always picks the only candidate - kept, not deleted, so
# restoring route diversity later (a fixed pgr_ksp, or a different K-shortest-
# paths approach) is a one-line K_ROUTES change, not rebuilding this too.
ROUTE_CHOICE_WEIGHTS = [60, 25, 15]

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

KSP_SQL = """
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
    SELECT path_id, edge, cost
      FROM pgr_ksp(
               %(edges_sql)s,
               (SELECT id FROM start_vertex),
               (SELECT id FROM end_vertex),
               %(k)s,
               directed => true
           )
     WHERE edge > 0
"""

# gid is ways' own primary key, so this is an indexed lookup, not a scan -
# cheap relative to the pgr_ksp call itself.
ROUTE_GEOMETRY_SQL = """
    SELECT COALESCE(SUM(length_m) / 1000.0, 0) AS route_km,
           ST_AsText(ST_LineMerge(ST_Collect(the_geom))) AS route_wkt
      FROM ways
     WHERE gid = ANY(%(gids)s)
"""

# How many times a single route()-path query gets tried before this call
# gives up. A dead connection (confirmed live: both a clean statement_timeout
# cancellation and a backend dying underneath the query show up here) is
# usually transient - postgres.py's own pool discards the broken connection
# and hands out a fresh one on the very next checkout, so a retry against
# that fresh connection frequently succeeds where the first attempt didn't.
ROUTE_QUERY_ATTEMPTS = 3
# A short pause before each retry, not zero: if a whole node just OOM-killed
# and is still "the database system is in recovery mode" (confirmed live to
# last a few seconds), retrying instantly just hits the same still-recovering
# node again. This gives HAProxy's own health check a real chance to have
# already failed it over to the other replica by the next attempt.
ROUTE_RETRY_DELAY_S = 0.5


def _query_with_retry(fetch, sql: str, params: dict, what: str, **log_extra):
    """Run one route()-path query, retrying past a transient connection
    failure before this specific trip is given up on. Raises the last
    psycopg.OperationalError if every attempt fails - the caller treats
    that the same as no path found, not a crash (see route()'s docstring)."""
    last_exc: psycopg.OperationalError | None = None
    for attempt in range(1, ROUTE_QUERY_ATTEMPTS + 1):
        try:
            with postgres.read_connection() as conn:
                return fetch(conn, sql, params)
        except psycopg.OperationalError as exc:
            last_exc = exc
            if attempt < ROUTE_QUERY_ATTEMPTS:
                log.warning(
                    f"{what} failed, retrying",
                    extra={**log_extra, "attempt": attempt, "of": ROUTE_QUERY_ATTEMPTS, "error": str(exc)},
                )
                time.sleep(ROUTE_RETRY_DELAY_S)
    raise last_exc


def route(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float, period: str
) -> tuple[float, int, str | None] | None:
    """Return (kilometres, seconds, route as text) or None if there is no path.

    The nearest street corner to each point is used as the start and the end.
    Somebody standing in the middle of a park still gets a route: it begins
    at the nearest place a car can be.

    None means the two points are not connected in the imported map - usually
    a point outside the imported area - or, on a stack still running with
    K_ROUTES>1 somehow, that this specific call's own connection died
    underneath it (pgRouting issue #1319 - see this module's own docstring).
    That failure is logged as an error with the exact coordinates, so a
    repeat is reproducible, and either way the caller treats it as "no
    driver found" for this one trip rather than crashing the whole run.

    Up to K_ROUTES candidate routes are computed (pgr_ksp), and one is
    picked with ROUTE_CHOICE_WEIGHTS - at the current K_ROUTES=1 this is
    always the one candidate pgr_ksp returns, functionally a plain
    pgr_dijkstra call. The pick is seeded from this call's own inputs
    rather than a shared random.Random, kept for when K_ROUTES is
    eventually restored above 1:
    this runs inside h-bootstrap's own ThreadPoolExecutor for the historical
    week (see history.py), where a shared generator would make which route
    gets picked depend on thread-scheduling order, breaking the same-seed-
    same-output guarantee the rest of this codebase relies on for debugging.
    A given trip's two points and period still always pick the same route on
    a repeat run.
    """
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}; expected one of {sorted(PERIODS)}")

    edges_sql = EDGES_SQL_TEMPLATE.format(period=period)

    try:
        rows = _query_with_retry(
            postgres.fetch_all,
            KSP_SQL,
            {
                "from_lat": from_lat, "from_lon": from_lon,
                "to_lat": to_lat, "to_lon": to_lon,
                "edges_sql": edges_sql,
                "k": K_ROUTES,
            },
            "pgr_ksp call",
            from_lat=from_lat, from_lon=from_lon,
            to_lat=to_lat, to_lon=to_lon, period=period,
        )
    except psycopg.OperationalError as exc:
        # Confirmed live that both failure modes land here: a clean
        # statement_timeout cancellation, and a backend dying underneath
        # the query entirely ("server closed the connection unexpectedly").
        # Every one of ROUTE_QUERY_ATTEMPTS already failed by the time this
        # runs - logged with the exact coordinates and the real exception
        # text so a repeat is reproducible, and treated the same as no
        # path found rather than crashing the whole run over one trip.
        log.error(
            "pgr_ksp call failed after retries - treating as no route found",
            extra={
                "from_lat": from_lat, "from_lon": from_lon,
                "to_lat": to_lat, "to_lon": to_lon, "period": period,
                "attempts": ROUTE_QUERY_ATTEMPTS, "error": str(exc),
            },
        )
        return None

    if not rows:
        return None

    candidates: dict[int, dict] = {}
    for row in rows:
        candidate = candidates.setdefault(row["path_id"], {"gids": [], "cost_s": 0.0})
        candidate["gids"].append(row["edge"])
        candidate["cost_s"] += float(row["cost"])

    path_ids = sorted(candidates)
    weights = [
        ROUTE_CHOICE_WEIGHTS[i] if i < len(ROUTE_CHOICE_WEIGHTS) else ROUTE_CHOICE_WEIGHTS[-1]
        for i in range(len(path_ids))
    ]
    # hashlib, not Python's built-in hash(): a plain tuple containing the
    # period string would hash differently on every process (PYTHONHASHSEED
    # randomizes str hashing by default), which would silently break the
    # same-inputs-same-route guarantee this docstring promises across runs,
    # not just within one - confirmed live, hash() on a tuple also just
    # rejects being used as a seed directly.
    seed_key = f"{from_lat}:{from_lon}:{to_lat}:{to_lon}:{period}".encode()
    seed = int(hashlib.md5(seed_key).hexdigest(), 16)
    chooser = random.Random(seed)
    chosen_id = chooser.choices(path_ids, weights=weights, k=1)[0]
    chosen = candidates[chosen_id]

    try:
        geo_row = _query_with_retry(
            postgres.fetch_one,
            ROUTE_GEOMETRY_SQL,
            {"gids": chosen["gids"]},
            "route geometry lookup",
            from_lat=from_lat, from_lon=from_lon,
            to_lat=to_lat, to_lon=to_lon, period=period,
        )
    except psycopg.OperationalError as exc:
        log.error(
            "route geometry lookup failed after retries - treating as no route found",
            extra={
                "from_lat": from_lat, "from_lon": from_lon,
                "to_lat": to_lat, "to_lon": to_lon, "period": period,
                "gids": len(chosen["gids"]), "attempts": ROUTE_QUERY_ATTEMPTS, "error": str(exc),
            },
        )
        return None

    if not geo_row or not geo_row["route_km"]:
        return None

    seconds = int(chosen["cost_s"])
    # A route that claims to take no time is a broken cost column, not a
    # teleport. Fall back to a plain speed estimate so the trip still works.
    if seconds <= 0:
        seconds = int(float(geo_row["route_km"]) / 25.0 * 3600)

    return float(geo_row["route_km"]), seconds, geo_row["route_wkt"]


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


def build_road_point_pools(grid, pool_size: int, workers: int, seed_value: int = 20250824) -> None:
    """Precompute a pool of real, road-snapped points for every servicable
    zone, once.

    random_road_point_in_zone's own snapping step is a real database round
    trip (nearest_road_point) - calling it directly once per driver, once
    per passenger, once per trip pickup/dropoff adds up to millions of
    sequential round trips at full scale, confirmed directly to be most of
    what made h-bootstrap's own people-seeding step take tens of minutes
    with the host otherwise idle. A modest pool per zone, sampled from in
    memory afterward (pooled_road_point_in_zone), turns that into a
    bounded, one-time cost of zones x pool_size calls instead - threaded,
    since these are independent, I/O-bound calls.

    Every caller that wants the fast path calls this once, at its own
    startup - h-bootstrap, driver-service and passenger-service each build
    their own pool in their own process; there is no shared cache across
    processes, the same as CityGrid itself.
    """
    global _road_point_pools
    zone_ids = grid.all_zone_ids()

    def _one_zone(zone_id: str) -> tuple[str, list[tuple[float, float]]]:
        rng = random.Random(f"{seed_value}:{zone_id}:road-point-pool")
        points = [random_road_point_in_zone(grid, zone_id, rng) for _ in range(pool_size)]
        return zone_id, points

    pools: dict[str, list[tuple[float, float]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for zone_id, points in executor.map(_one_zone, zone_ids):
            pools[zone_id] = points

    _road_point_pools = pools
    log.info(
        "road point pools ready",
        extra={"zones": len(pools), "points_per_zone": pool_size},
    )


def pooled_road_point_in_zone(grid, zone_id: str, rng) -> tuple[float, float]:
    """A real, road-snapped point in this zone, sampled from the pool
    build_road_point_pools already built - no database round trip.

    Falls back to a direct snap (the real cost random_road_point_in_zone
    always paid) if the pool was never built or does not cover this zone,
    so a caller that skips build_road_point_pools still gets a correct
    answer, just not the fast path.
    """
    pool = _road_point_pools.get(zone_id)
    if not pool:
        return random_road_point_in_zone(grid, zone_id, rng)
    return rng.choice(pool)


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
