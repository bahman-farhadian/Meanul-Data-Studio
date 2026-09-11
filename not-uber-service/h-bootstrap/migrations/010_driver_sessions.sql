-- When a driver's shift started and ended - today only status exists,
-- which says what a driver is doing right now and remembers nothing about
-- when they went online. driver-service already knows the exact moment a
-- shift starts and ends (its own shift-change tick); this is where that
-- moment gets kept. Feeds the fleet-utilization rollup in ClickHouse and
-- gives the dispatch-saturation question this project already had to
-- answer by hand ("how many drivers were actually online this hour") a
-- real table to ask instead.

CREATE TABLE IF NOT EXISTS driver_sessions (
    session_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    driver_id   text        NOT NULL REFERENCES drivers(driver_id),
    started_at  timestamptz NOT NULL,
    -- NULL while the shift is still open - driver-service closes it when
    -- the driver goes offline again.
    ended_at    timestamptz,
    zone_id     text        NOT NULL
);

CREATE INDEX IF NOT EXISTS driver_sessions_driver_idx ON driver_sessions (driver_id, started_at DESC);
-- Unique, not just indexed: a driver going offline twice without going
-- online in between would be a real bug in driver-service's own shift-
-- change logic, and this is what turns that bug into a loud constraint
-- violation instead of a second silently-open session nobody notices.
CREATE UNIQUE INDEX IF NOT EXISTS driver_sessions_one_open_idx ON driver_sessions (driver_id) WHERE ended_at IS NULL;
