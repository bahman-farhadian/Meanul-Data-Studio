-- What survives after the raw positions expire.
--
-- The two position tables are the whole disk problem. At full scale
-- driver_positions alone projects to 57 GiB per node and rider_positions,
-- once the fleet fix made trips actually complete, went from 6 GiB to 13.
-- The obvious lever is retention, and it is the wrong one on its own:
-- cutting a TTL does not reduce what is stored, it deletes what was
-- answered. Seven days of rider positions became two and the questions
-- that needed day three simply stopped having answers.
--
-- So the raw rows keep a short life and the ANSWER keeps a long one. Both
-- position tables now expire in two days, and these two rollups hold what
-- the raw rows were being kept for, at a size that does not scale with the
-- fleet:
--
--     256 zones x 24 hours = 6,144 rows a day, for either table,
--     whether the fleet is 4,000 cars or 106,000.
--
-- That is the property worth having. The raw stream is bounded by the
-- ticking of every device; the rollup is bounded by the city.
--
-- SUMS AND STATES, NEVER A STORED AVERAGE - the same rule every other
-- rollup here follows. speed_kmh_sum with speed_samples beside it, divided
-- at query time. An average written into a SummingMergeTree is an average
-- of partial rows and drifts with merge history.
--
-- AggregatingMergeTree rather than SummingMergeTree because "how many
-- distinct drivers" cannot be summed: two hours with the same driver in
-- both is one driver, not two. uniqState keeps the sketch; uniqMerge reads
-- it. SimpleAggregateFunction carries the plain sums and maxima in the
-- same table without a state wrapper, which is cheaper to merge and
-- cheaper to read.
--
-- No TTL, matching trip_stats_hourly and fulfilment_hourly. At 6,144 rows
-- a day and roughly sixty bytes a row this is about 130 MB a year per
-- node, which is not worth expiring and is worth keeping.

CREATE TABLE IF NOT EXISTS nus.driver_activity_hourly
(
    hour                DateTime('UTC'),
    zone_id             LowCardinality(String),

    positions           SimpleAggregateFunction(sum, UInt64),
    -- Not summable, hence a state: the same driver in two hours is one
    -- driver. This is the only column here that could not be a plain sum.
    drivers             AggregateFunction(uniq, FixedString(11)),

    -- The status mix, which is what makes this a fleet-presence table
    -- rather than a row count. offline never reaches the warehouse, so
    -- these three are the whole population.
    idle_positions      SimpleAggregateFunction(sum, UInt64),
    en_route_positions  SimpleAggregateFunction(sum, UInt64),
    on_trip_positions   SimpleAggregateFunction(sum, UInt64),

    speed_kmh_sum       SimpleAggregateFunction(sum, Float64),
    speed_samples       SimpleAggregateFunction(sum, UInt64),
    max_speed_kmh       SimpleAggregateFunction(max, Float32)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.driver_activity_hourly_mv
TO nus.driver_activity_hourly
AS
SELECT
    toStartOfHour(event_time)                    AS hour,
    zone_id,
    count()                                      AS positions,
    uniqState(driver_id)                         AS drivers,
    countIf(status = 'idle')                     AS idle_positions,
    countIf(status = 'en_route_pickup')          AS en_route_positions,
    countIf(status = 'on_trip')                  AS on_trip_positions,
    sum(toFloat64(ifNull(speed_kmh, 0)))         AS speed_kmh_sum,
    countIf(speed_kmh IS NOT NULL)               AS speed_samples,
    max(ifNull(speed_kmh, toFloat32(0)))         AS max_speed_kmh
FROM nus.driver_positions
GROUP BY hour, zone_id;


CREATE TABLE IF NOT EXISTS nus.rider_activity_hourly
(
    hour                DateTime('UTC'),
    zone_id             LowCardinality(String),

    positions           SimpleAggregateFunction(sum, UInt64),
    riders              AggregateFunction(uniq, FixedString(11)),
    -- A rider only reports while on a trip, so this doubles as "trips in
    -- flight through this zone" without joining anything.
    trips               AggregateFunction(uniq, Nullable(FixedString(21))),

    -- How good the phone's fix was. Summed with its own count, because a
    -- mean accuracy is the number anyone actually wants and a stored one
    -- could not be re-aggregated across hours.
    accuracy_m_sum      SimpleAggregateFunction(sum, Float64),
    accuracy_samples    SimpleAggregateFunction(sum, UInt64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.rider_activity_hourly_mv
TO nus.rider_activity_hourly
AS
SELECT
    toStartOfHour(event_time)                    AS hour,
    zone_id,
    count()                                      AS positions,
    uniqState(rider_id)                          AS riders,
    uniqState(trip_id)                           AS trips,
    sum(toFloat64(ifNull(accuracy_m, 0)))        AS accuracy_m_sum,
    countIf(accuracy_m IS NOT NULL)              AS accuracy_samples
FROM nus.rider_positions
GROUP BY hour, zone_id;


-- Reading them:
--
--   distinct drivers seen in a zone   uniqMerge(drivers)
--   mean speed                        sum(speed_kmh_sum) / sum(speed_samples)
--   share of time on a trip           sum(on_trip_positions) / sum(positions)
--   mean phone accuracy               sum(accuracy_m_sum) / sum(accuracy_samples)
