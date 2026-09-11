-- The two extensions the map depends on.
--
-- PostGIS teaches PostgreSQL about points, lines and polygons, and how to
-- index them. pgRouting builds on top of it and answers "what is the best
-- path from here to there" over a road network.
--
-- The database image already contains both; this only switches them on for
-- this database. Both statements are safe to run again.
--
-- Explicit SCHEMA public, not the database's own default search_path
-- ("nus", public - see bootstrap/database.py): the routable graph is built
-- once, separately, in a throwaway database with no custom schema at all
-- (h-bootstrap/lion-prepare/), so pgrouting/postgis live in that database's
-- public schema there, and pg_dump hardcodes that into the dump's DDL as
-- public.geometry, public.ways, etc. Installing unqualified here would put
-- them in "nus" instead, and pg_restore's schema-qualified statements would
-- fail against a public schema that never got PostGIS at all.
CREATE EXTENSION IF NOT EXISTS postgis SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgrouting SCHEMA public;
