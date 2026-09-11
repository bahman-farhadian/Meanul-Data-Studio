-- Real NYC TLC trip-record demand, not a random or hash-derived guess.
--
-- Built once, on the host, by zone-demand-prepare (mirrors lion-prepare's
-- own shape: a throwaway step that touches real external data once, dumps
-- a small derived result, and is thrown away) from a real month of TLC's
-- published High-Volume For-Hire Vehicle trip records. zone_demand_
-- calibration answers "how busy is this zone at this hour on this day of
-- the week, relative to average" - weight 1.0 is an ordinary hour for an
-- ordinary zone, the same semantic bootstrap/history.py's old
-- HOUR_WEIGHTS and passenger_service/demand.py's old hash-derived
-- zone_popularity() already used, now grounded in what riders actually
-- did instead of a guess.
--
-- day_of_week follows Python's own convention (pandas .dt.dayofweek,
-- which this table was built with): 0 = Monday .. 6 = Sunday.

CREATE TABLE IF NOT EXISTS zone_demand_calibration (
    zone_id      text    NOT NULL REFERENCES city_zones(zone_id),
    hour_of_day  smallint NOT NULL,
    day_of_week  smallint NOT NULL,
    -- Relative to the overall average trip volume across every zone/hour/
    -- day in the calibration month - not a raw count, so the shape
    -- transfers to any SEED_* scale.
    weight       numeric(8, 4) NOT NULL,
    PRIMARY KEY (zone_id, hour_of_day, day_of_week),
    CONSTRAINT zone_demand_calibration_hour_check CHECK (hour_of_day BETWEEN 0 AND 23),
    CONSTRAINT zone_demand_calibration_dow_check CHECK (day_of_week BETWEEN 0 AND 6)
);

-- Real pickup-zone -> dropoff-zone shares, from the same real month -
-- what OD pairs are actually common, not just "closer zones are more
-- likely" (the existing distance-decay model, which stays as the
-- fallback for a pair this real sample never saw).
CREATE TABLE IF NOT EXISTS od_pair_calibration (
    pickup_zone_id   text NOT NULL REFERENCES city_zones(zone_id),
    dropoff_zone_id  text NOT NULL REFERENCES city_zones(zone_id),
    -- Share of this pickup zone's own trips that went to this dropoff
    -- zone - sums to ~1.0 within one pickup_zone_id, so it composes with
    -- however many trips a zone actually generates rather than carrying
    -- its own absolute scale.
    trip_share       numeric(8, 6) NOT NULL,
    avg_fare         numeric(10, 2),
    avg_duration_s   integer,
    PRIMARY KEY (pickup_zone_id, dropoff_zone_id)
);

-- No separate indexes on either table: each primary key's own leading
-- column(s) already serve "every hour/day for this zone" and "every
-- dropoff for this pickup zone" lookups (the standard B-tree
-- leftmost-prefix rule) - a same-column index would just be dead weight.
