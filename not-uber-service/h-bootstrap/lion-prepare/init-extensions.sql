-- Run automatically by the postgis/postgis image's own
-- docker-entrypoint-initdb.d mechanism, once, when lion-pg's empty volume is
-- first created.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;
