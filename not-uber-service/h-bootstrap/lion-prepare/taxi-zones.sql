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

CREATE TABLE city_zones_source AS
SELECT
    locationid AS zone_id,
    zone        AS zone_name,
    borough,
    geom        AS boundary
FROM taxi_zones_raw;

ALTER TABLE city_zones_source ADD PRIMARY KEY (zone_id);
CREATE INDEX city_zones_source_boundary_idx ON city_zones_source USING gist (boundary);
