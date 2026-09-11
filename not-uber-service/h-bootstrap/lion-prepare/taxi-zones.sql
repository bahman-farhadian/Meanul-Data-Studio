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

-- A first attempt here just GROUP BY'd locationid and ST_Union'd the
-- duplicates, on the assumption that a repeated id meant one real zone
-- split into disconnected parts. Checked directly against the actual
-- downloaded GeoJSON and TLC's own taxi_zone_lookup.csv afterwards, and
-- that assumption was wrong: it produced 260 zones instead of 263, not
-- 263 - the duplicates are a real, documented quirk of TLC's shapefile
-- itself, not a same-zone split. locationid 56 has two polygon features
-- but 56 AND 57 are both officially "Corona, Queens" in TLC's own lookup;
-- 103 has three polygon features but 103, 104 AND 105 are all officially
-- "Governor's Island/Ellis Island/Liberty Island, Manhattan". The
-- shapefile simply never labels the extra parts with their real official
-- ids, and nothing in any published source says which physical polygon
-- is which id - merging them away is exactly as wrong as leaving 57/104/
-- 105 missing entirely, which is what happened before this fix.
--
-- The only defensible move without an authoritative per-polygon mapping:
-- assign each duplicate's parts to its known sibling ids deterministically
-- (largest part keeps the lower id, so a real, non-trivial shape lands on
-- every official id) - restricted explicitly to these two confirmed
-- cases, not a general "N duplicates get N consecutive ids" rule, so an
-- unrelated duplicate in some future TLC refresh fails loudly instead of
-- being silently mis-split by a rule that happened to work here by
-- coincidence.
-- No ON COMMIT DROP: this script runs one statement per autocommitted
-- transaction under psql -f (confirmed directly - ON COMMIT DROP dropped
-- the table before the very next INSERT could see it), not inside one
-- transaction block. A plain TEMP table is cleaned up when the session
-- ends regardless, which is the whole rest of this same psql run.
CREATE TEMP TABLE zone_id_extras (base_id int PRIMARY KEY, extra_ids int[]);
INSERT INTO zone_id_extras VALUES (56, ARRAY[57]), (103, ARRAY[104, 105]);

DO $$
DECLARE
    unexpected int;
BEGIN
    SELECT locationid INTO unexpected
    FROM taxi_zones_raw
    GROUP BY locationid
    HAVING count(*) > 1 AND locationid NOT IN (SELECT base_id FROM zone_id_extras)
    LIMIT 1;
    IF unexpected IS NOT NULL THEN
        RAISE EXCEPTION 'taxi_zones_raw has an unrecognized duplicate locationid % - not one of the known TLC shapefile quirks (56, 103). Check whether this is the same kind of split before adding it to zone_id_extras.', unexpected;
    END IF;
END $$;

CREATE TABLE city_zones_source AS
WITH ranked AS (
    SELECT
        locationid, zone, borough, geom,
        row_number() OVER (PARTITION BY locationid ORDER BY ST_Area(geom) DESC) AS part_rank
    FROM taxi_zones_raw
)
SELECT
    CASE
        WHEN r.part_rank = 1 THEN r.locationid
        ELSE (SELECT e.extra_ids[r.part_rank - 1] FROM zone_id_extras e WHERE e.base_id = r.locationid)
    END           AS zone_id,
    r.zone         AS zone_name,
    r.borough,
    ST_Multi(r.geom) AS boundary
FROM ranked r;

ALTER TABLE city_zones_source ADD PRIMARY KEY (zone_id);
CREATE INDEX city_zones_source_boundary_idx ON city_zones_source USING gist (boundary);
