-- The two extensions the map depends on.
--
-- PostGIS teaches PostgreSQL about points, lines and polygons, and how to
-- index them. pgRouting builds on top of it and answers "what is the best
-- path from here to there" over a road network.
--
-- The database image already contains both; this only switches them on for
-- this database. Both statements are safe to run again.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;
