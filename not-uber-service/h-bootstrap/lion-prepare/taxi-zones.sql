-- Turns the raw TLC Taxi Zones import into the clean table h-bootstrap
-- restores into city_zones.
--
-- Runs once, inside the same throwaway lion-pg container that builds the
-- routable graph - never against the real stack. Its whole output is one
-- table, city_zones_source, dumped alongside ways/ways_vertices_pgr and
-- thrown away with the container that built it.
--
-- ogr2ogr's PostgreSQL driver lowercases every column name by default
-- (verified directly, same as LION's own import): locationid/zone/borough
-- come out lowercase with no quoting needed.

-- Idempotent, same reasoning as build-graph.sql's own step 0: a retry
-- after a previous failed or interrupted run can find this table already
-- sitting in lion-pg's volume.
DROP TABLE IF EXISTS city_zones_source CASCADE;

-- GROUP BY locationid, not a plain SELECT: TLC's real GeoJSON genuinely
-- has more than one feature for a handful of zone ids - confirmed directly
-- against a live import (locationid 56 duplicated, ADD PRIMARY KEY failing
-- on it). Real ride-hailing zones can be legitimately non-contiguous, so
-- this merges every part into one proper multi-part boundary instead of
-- picking one row and silently dropping the other's geometry - the same
-- ST_Union pattern would still be correct even if a duplicate turned out
-- to be an exact repeat rather than a second real part.
CREATE TABLE city_zones_source AS
SELECT
    locationid               AS zone_id,
    max(zone)                AS zone_name,
    max(borough)             AS borough,
    ST_Multi(ST_Union(geom)) AS boundary
FROM taxi_zones_raw
GROUP BY locationid;

ALTER TABLE city_zones_source ADD PRIMARY KEY (zone_id);
CREATE INDEX city_zones_source_boundary_idx ON city_zones_source USING gist (boundary);
