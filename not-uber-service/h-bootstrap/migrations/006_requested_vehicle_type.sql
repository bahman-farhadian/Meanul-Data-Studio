-- What tier of car a rider actually asked for.
--
-- A real matching input, not a display field: dispatch-service filters
-- candidate drivers by this (see l-service-dispatch, the per-tier Redis GEO
-- sets), so it belongs on the row, not buried in trips.attributes jsonb.

ALTER TABLE trips ADD COLUMN IF NOT EXISTS requested_vehicle_type text NOT NULL DEFAULT 'economy';

ALTER TABLE trips DROP CONSTRAINT IF EXISTS trips_requested_vehicle_type_check;
ALTER TABLE trips ADD CONSTRAINT trips_requested_vehicle_type_check
    CHECK (requested_vehicle_type IN ('economy', 'xl', 'premium'));
