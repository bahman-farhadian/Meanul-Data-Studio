-- Turns the raw LION import into the routable graph pgRouting needs.
--
-- Runs once, inside the throwaway lion-pg container `make lion-prepare`
-- brings up - never against the real stack. Its whole output is two tables,
-- ways and ways_vertices_pgr, dumped to a file and thrown away with the
-- container that built them.

-- 0. Idempotent: a retry after a previous failed or interrupted run can
-- find lion-pg's container (and its volume) still around - its image was
-- unchanged, so `docker compose up` does not recreate it - with these
-- tables already sitting there from that attempt. Drop them first so a
-- retry always builds from lion_raw fresh instead of erroring on "already
-- exists". Nothing here changes what gets filtered or how costs are
-- computed - it only clears stale output from a run that did not finish.
DROP TABLE IF EXISTS lion_filtered CASCADE;
DROP TABLE IF EXISTS ways CASCADE;
DROP TABLE IF EXISTS ways_vertices_pgr CASCADE;

-- 1. Keep only real, drivable street geometry.
--
-- FeatureTyp: 0 = real street, 6 = private street, A = alley - all three are
-- actually drivable. Excluded: 1 railroad, 2 water edge/shoreline, 5 paper
-- street (legally mapped, never built), 7/8/9 non-street boundaries, F ferry
-- route, and anything else not in this list.
--
-- SegmentTyp: keep undivided (U), both-directions-on-one-bed (B), roadbed
-- (R), connector (C) and ramp (E) segments - the pieces a car actually
-- drives on. Drop the generic (G) centerline, which is an imaginary
-- duplicate of a divided road's roadbeds, plus terminator (T) and
-- suppressed (S) segments.
--
-- TrafDir: W/A/T are vehicle directions. P (pedestrian path) and blank
-- (non-street feature) are excluded outright - nothing here is for cars.
-- ogr2ogr's PostgreSQL driver lowercases every column name by default
-- (verified directly: OBJECTID/FeatureTyp/... come out as
-- objectid/featuretyp/... with no quoting needed) - lion_raw's columns are
-- addressed unquoted-lowercase throughout this file for that reason, not
-- because DCP's own metadata names them that way.
--
-- geom also comes out as MultiLineString even where every real segment is a
-- single line (LION's own metadata calls the feature type "Polyline",
-- which OGR treats as multi-part-capable) - ST_Dump splits any that
-- genuinely have more than one part into their own rows, with a synthetic
-- gid, so ways.the_geom stays plain LineString the way pgr_createTopology
-- and nus_common/routing.py's ST_Collect/ST_LineMerge both expect.
CREATE TABLE lion_filtered AS
SELECT
    row_number() OVER ()   AS gid,
    trafdir                AS traf_dir,
    posted_speed,
    (ST_Dump(geom)).geom   AS geom
FROM lion_raw
WHERE featuretyp IN ('0', '6', 'A')
  AND segmenttyp IN ('U', 'R', 'C', 'E', 'B')
  AND trafdir IN ('W', 'A', 'T')
  AND geom IS NOT NULL;

-- 2. Cost in seconds, from real length and the real posted speed limit -
-- not an assumed per-road-class speed. NYC's citywide default (25 mph)
-- covers the segments with a blank POSTED_SPEED - some genuinely empty,
-- some whitespace-only (verified directly against a real LION import:
-- NULLIF alone does not catch "  ", TRIM is needed first).
ALTER TABLE lion_filtered ADD COLUMN length_m double precision;
UPDATE lion_filtered SET length_m = ST_Length(geom::geography);

ALTER TABLE lion_filtered ADD COLUMN cost_s double precision;
ALTER TABLE lion_filtered ADD COLUMN reverse_cost_s double precision;

-- -1 is pgRouting's convention for "this direction cannot be driven" - the
-- same convention the previous osm2pgrouting-built graph already used for
-- one-way streets, so nus_common/routing.py needs no change here.
UPDATE lion_filtered SET
    cost_s = CASE
        WHEN traf_dir = 'A' THEN -1
        ELSE length_m / (GREATEST(COALESCE(NULLIF(TRIM(posted_speed), '')::numeric, 25), 5) * 1609.34 / 3600.0)
    END,
    reverse_cost_s = CASE
        WHEN traf_dir = 'W' THEN -1
        ELSE length_m / (GREATEST(COALESCE(NULLIF(TRIM(posted_speed), '')::numeric, 25), 5) * 1609.34 / 3600.0)
    END;

-- 3. The table shape pgr_createTopology expects, matching the column names
-- nus_common/routing.py already queries (the_geom, cost_s, reverse_cost_s,
-- length_m, source, target).
--
-- geom::geometry(LineString, 4326), not a bare geom AS the_geom: ST_Dump's
-- own composite-type signature returns .geom as a generic, untyped
-- geometry (no SRID/subtype in its typmod), and that genericness survives
-- straight through this CTAS otherwise - confirmed directly against a real
-- import (geometry_columns showed the_geom as type=GEOMETRY, srid=0). The
-- coordinate data itself is already correct SRID 4326 from ogr2ogr's
-- -t_srs; this cast only restores the column's own type modifier, which is
-- what geometry_columns and any GIS tool (DBeaver included) actually read
-- to know it can render this as a spatial layer.
CREATE TABLE ways AS
SELECT
    gid,
    geom::geometry(LineString, 4326) AS the_geom,
    cost_s,
    reverse_cost_s,
    length_m,
    NULL::bigint AS source,
    NULL::bigint AS target
FROM lion_filtered;

ALTER TABLE ways ADD PRIMARY KEY (gid);
CREATE INDEX ways_geom_idx ON ways USING gist (the_geom);

-- Tolerance ~1.1m at this latitude: endpoints closer than this are the same
-- intersection. Builds ways_vertices_pgr and fills ways.source/target.
--
-- Verified end to end against a real pgRouting 3.8 instance (this stack
-- pins its own throwaway lion-pg image, so it does not depend on whatever
-- version the deployed pg-* cluster happens to run). pgRouting 3.8 prints a
-- deprecation warning for this function but it still works correctly - if a
-- future lion-pg bump moves to pgRouting 4.x and it is actually removed,
-- that is the one line in this whole file that needs to change.
SELECT pgr_createTopology('ways', 0.00001, 'the_geom', 'gid');

CREATE INDEX ways_vertices_pgr_geom_idx ON ways_vertices_pgr USING gist (the_geom);

-- 4. Mark which vertices are actually reachable from each other - the
-- verified, connected core every generated point gets snapped to
-- (nus_common/routing.py::nearest_road_point). Real islands (Governors,
-- Liberty, Ellis - no car bridge) correctly stay unmarked; nothing here
-- changes that, it only stops the rest of the map from paying for it.
ALTER TABLE ways_vertices_pgr ADD COLUMN on_main_network boolean NOT NULL DEFAULT false;

WITH components AS (
    SELECT * FROM pgr_strongComponents(
        'SELECT gid AS id, source, target, cost_s AS cost, reverse_cost_s AS reverse_cost FROM ways'
    )
),
main AS (
    SELECT component FROM components GROUP BY component ORDER BY count(*) DESC LIMIT 1
)
UPDATE ways_vertices_pgr v
   SET on_main_network = true
  FROM components c, main m
 WHERE c.node = v.id AND c.component = m.component;

CREATE INDEX ways_vertices_pgr_main_geom_idx
    ON ways_vertices_pgr USING gist (the_geom)
    WHERE on_main_network;
