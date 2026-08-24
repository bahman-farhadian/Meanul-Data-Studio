-- Indexes.
--
-- Kept in their own file, after the tables, so the shape of the data and the
-- decisions about speed can be read separately.

-- Spatial indexes. A GiST index is what makes "which zone contains this
-- point" and "which trips started near here" fast instead of a full scan.
CREATE INDEX IF NOT EXISTS city_zones_boundary_idx  ON city_zones USING gist (boundary);
CREATE INDEX IF NOT EXISTS trips_pickup_point_idx   ON trips      USING gist (pickup_point);
CREATE INDEX IF NOT EXISTS trips_dropoff_point_idx  ON trips      USING gist (dropoff_point);

-- The questions the services ask most often.
CREATE INDEX IF NOT EXISTS drivers_status_idx       ON drivers (status);
CREATE INDEX IF NOT EXISTS drivers_home_zone_idx    ON drivers (home_zone_id);
CREATE INDEX IF NOT EXISTS trips_status_idx         ON trips (status);
CREATE INDEX IF NOT EXISTS trips_requested_at_idx   ON trips (requested_at DESC);
CREATE INDEX IF NOT EXISTS trips_driver_idx         ON trips (driver_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS trips_rider_idx          ON trips (rider_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS trips_pickup_zone_idx    ON trips (pickup_zone_id, requested_at DESC);

-- Looking inside the jsonb columns. A GIN index makes "every driver whose
-- vehicle is electric" a real query rather than a full scan.
CREATE INDEX IF NOT EXISTS drivers_vehicle_gin_idx    ON drivers    USING gin (vehicle);
CREATE INDEX IF NOT EXISTS passengers_prefs_gin_idx   ON passengers USING gin (preferences);
CREATE INDEX IF NOT EXISTS trips_attributes_gin_idx   ON trips      USING gin (attributes);

-- Traffic is always read for one part of the day at a time.
CREATE INDEX IF NOT EXISTS segment_traffic_period_idx ON segment_traffic (period);
