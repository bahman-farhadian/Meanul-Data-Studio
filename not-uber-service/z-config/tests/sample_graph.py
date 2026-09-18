"""A tiny connected street grid plus a harbour and a disconnected island.

Enough to prove a cab cannot be placed in the water and that a route walks
the L of the grid, not the hypotenuse. Coordinates are WGS84 around
midtown-ish numbers so geography lengths are real metres.
"""

from __future__ import annotations

LATS = (40.7500, 40.7510, 40.7520, 40.7530)
LONS = (-73.9900, -73.9890, -73.9880, -73.9870)

# East of the grid, no streets. ~1.5 km from the nearest avenue.
WATER_LAT = 40.7515
WATER_LON = -73.9720

# A real but unreachable island street, further east.
ISLAND_LAT = 40.7515
ISLAND_LON = -73.9600


def vertex_id(row: int, col: int) -> int:
    return row * len(LONS) + col + 1


def load(conn) -> None:
    """Create pgRouting tables and fill the sample city. Idempotent."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
        cur.execute("DROP TABLE IF EXISTS trips")
        cur.execute("DROP TABLE IF EXISTS city_zones")
        cur.execute("DROP TABLE IF EXISTS ways CASCADE")
        cur.execute("DROP TABLE IF EXISTS ways_vertices_pgr CASCADE")
        cur.execute("DROP TABLE IF EXISTS segment_traffic")
        cur.execute(
            """
            CREATE TABLE segment_traffic (
                way_id bigint NOT NULL,
                period text NOT NULL,
                congestion_factor double precision NOT NULL DEFAULT 1.0,
                sample_count integer NOT NULL DEFAULT 0,
                PRIMARY KEY (way_id, period)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE ways (
                gid bigint PRIMARY KEY,
                the_geom geometry(LineString, 4326) NOT NULL,
                cost_s double precision NOT NULL,
                reverse_cost_s double precision NOT NULL,
                length_m double precision NOT NULL,
                source bigint,
                target bigint
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE ways_vertices_pgr (
                id bigint PRIMARY KEY,
                the_geom geometry(Point, 4326) NOT NULL,
                on_main_network boolean NOT NULL DEFAULT false
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE city_zones (
                zone_id text PRIMARY KEY,
                name text NOT NULL,
                borough text NOT NULL,
                boundary geometry(MultiPolygon, 4326) NOT NULL,
                centroid geometry(Point, 4326) NOT NULL,
                servicable boolean NOT NULL DEFAULT true
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE trips (
                trip_id text PRIMARY KEY,
                pickup_point geometry(Point, 4326) NOT NULL,
                dropoff_point geometry(Point, 4326) NOT NULL,
                route geometry,
                route_km double precision,
                ended_at timestamptz
            )
            """
        )
        cur.execute("CREATE INDEX ways_geom_idx ON ways USING gist (the_geom)")
        cur.execute(
            "CREATE INDEX ways_vertices_pgr_geom_idx ON ways_vertices_pgr USING gist (the_geom)"
        )

        for row, lat in enumerate(LATS):
            for col, lon in enumerate(LONS):
                cur.execute(
                    """
                    INSERT INTO ways_vertices_pgr (id, the_geom, on_main_network)
                    VALUES (
                        %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                        true
                    )
                    """,
                    (vertex_id(row, col), lon, lat),
                )

        gid = 0
        for row, lat in enumerate(LATS):
            for col in range(len(LONS) - 1):
                gid += 1
                a, b = vertex_id(row, col), vertex_id(row, col + 1)
                _insert_edge(cur, gid, a, LONS[col], lat, b, LONS[col + 1], lat)
        for col, lon in enumerate(LONS):
            for row in range(len(LATS) - 1):
                gid += 1
                a, b = vertex_id(row, col), vertex_id(row + 1, col)
                _insert_edge(cur, gid, a, lon, LATS[row], b, lon, LATS[row + 1])

        cur.execute(
            """
            INSERT INTO ways_vertices_pgr (id, the_geom, on_main_network)
            VALUES (
                100,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                false
            )
            """,
            (ISLAND_LON, ISLAND_LAT),
        )
        cur.execute(
            """
            INSERT INTO ways (
                gid, the_geom, cost_s, reverse_cost_s, length_m, source, target
            )
            VALUES (
                100,
                ST_SetSRID(ST_MakeLine(
                    ST_MakePoint(%s, %s),
                    ST_MakePoint(%s, %s)
                ), 4326),
                30, 30, 80, 100, 100
            )
            """,
            (ISLAND_LON, ISLAND_LAT, ISLAND_LON + 0.0004, ISLAND_LAT),
        )

        # Land zone covers the grid and a strip of harbour so a random point
        # can land in water while the centroid still snaps to a street.
        cur.execute(
            """
            INSERT INTO city_zones (zone_id, name, borough, boundary, centroid, servicable)
            VALUES (
                '1',
                'sample-land',
                'Manhattan',
                ST_Multi(ST_GeomFromText(
                    'POLYGON((-73.991 40.749, -73.986 40.749, -73.986 40.754,
                              -73.978 40.754, -73.978 40.749, -73.991 40.749))',
                    4326
                )),
                ST_SetSRID(ST_MakePoint(-73.980, 40.7515), 4326),
                true
            )
            """
        )
    conn.commit()


def _insert_edge(cur, gid, source, lon1, lat1, target, lon2, lat2) -> None:
    cur.execute(
        """
        INSERT INTO ways (
            gid, the_geom, cost_s, reverse_cost_s, length_m, source, target
        )
        VALUES (
            %s,
            ST_SetSRID(ST_MakeLine(ST_MakePoint(%s, %s), ST_MakePoint(%s, %s)), 4326),
            ST_Length(
                ST_SetSRID(ST_MakeLine(ST_MakePoint(%s, %s), ST_MakePoint(%s, %s)), 4326)::geography
            ) / 8.0,
            ST_Length(
                ST_SetSRID(ST_MakeLine(ST_MakePoint(%s, %s), ST_MakePoint(%s, %s)), 4326)::geography
            ) / 8.0,
            ST_Length(
                ST_SetSRID(ST_MakeLine(ST_MakePoint(%s, %s), ST_MakePoint(%s, %s)), 4326)::geography
            ),
            %s, %s
        )
        """,
        (
            gid,
            lon1, lat1, lon2, lat2,
            lon1, lat1, lon2, lat2,
            lon1, lat1, lon2, lat2,
            lon1, lat1, lon2, lat2,
            source, target,
        ),
    )
