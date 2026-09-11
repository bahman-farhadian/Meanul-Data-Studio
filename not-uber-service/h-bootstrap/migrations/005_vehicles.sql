-- Vehicles, as their own entity instead of a jsonb blob on drivers.
--
-- Promoted out of drivers.vehicle (see 002_core_tables.sql) because two
-- real things need to read its structure, not just display it: dispatch
-- matching a request's tier against a real seat count, and the
-- fleet-composition rollups this project's ClickHouse schema is missing.

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id    text PRIMARY KEY,
    driver_id     text        NOT NULL REFERENCES drivers(driver_id),
    -- What dispatch actually matches a request's tier against.
    vehicle_type  text        NOT NULL DEFAULT 'economy',
    seats         integer     NOT NULL DEFAULT 4,
    make          text        NOT NULL,
    model         text        NOT NULL,
    year          integer,
    plate         text,
    colour        text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT vehicles_type_check CHECK (vehicle_type IN ('economy', 'xl', 'premium'))
);

CREATE INDEX IF NOT EXISTS vehicles_driver_idx ON vehicles (driver_id);
CREATE INDEX IF NOT EXISTS vehicles_type_idx   ON vehicles (vehicle_type);

-- Backfill from the jsonb blob every existing driver already has. One
-- vehicle per driver today - this project has no notion yet of a driver
-- switching cars mid-simulation, so driver_id doubles as a stable,
-- deterministic vehicle_id source.
INSERT INTO vehicles (vehicle_id, driver_id, make, model, year, plate, colour)
SELECT
    'veh-' || right(driver_id, 6),
    driver_id,
    vehicle->>'make',
    vehicle->>'model',
    (vehicle->>'year')::integer,
    vehicle->>'plate',
    vehicle->>'colour'
FROM drivers
WHERE vehicle ? 'make'
ON CONFLICT (vehicle_id) DO NOTHING;
