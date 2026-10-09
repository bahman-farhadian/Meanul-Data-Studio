-- Run once by the image entrypoint when lion-pg's empty volume is first
-- created. The image already contains both extensions.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;
