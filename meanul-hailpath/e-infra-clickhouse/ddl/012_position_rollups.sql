-- Hourly zone rollups that outlive driver_positions / rider_positions
-- (TTL 2 days). AggregatingMergeTree, monthly partitions, no TTL.
-- Keyed on (hour, zone_id) so the table is bounded by the city, not the
-- fleet. ClickHouse sum() returns UInt64, so SimpleAggregateFunction(sum)
-- stores UInt64.

CREATE TABLE IF NOT EXISTS hailpath.driver_activity_hourly
(
    hour                DateTime('UTC'),
    zone_id             UInt16,

    positions           SimpleAggregateFunction(sum, UInt64),
    -- uniq cannot be a plain sum: the same driver in two hours is one driver.
    drivers             AggregateFunction(uniq, UInt32),

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

CREATE MATERIALIZED VIEW IF NOT EXISTS hailpath.driver_activity_hourly_mv
TO hailpath.driver_activity_hourly
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
FROM hailpath.driver_positions
GROUP BY hour, zone_id;


CREATE TABLE IF NOT EXISTS hailpath.rider_activity_hourly
(
    hour                DateTime('UTC'),
    zone_id             UInt16,

    positions           SimpleAggregateFunction(sum, UInt64),
    riders              AggregateFunction(uniq, UInt32),
    trips               AggregateFunction(uniq, UInt64),
    accuracy_m_sum      SimpleAggregateFunction(sum, Float64),
    accuracy_samples    SimpleAggregateFunction(sum, UInt64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS hailpath.rider_activity_hourly_mv
TO hailpath.rider_activity_hourly
AS
SELECT
    toStartOfHour(event_time)                    AS hour,
    zone_id,
    count()                                      AS positions,
    uniqState(rider_id)                          AS riders,
    uniqState(trip_id)                           AS trips,
    sum(toFloat64(ifNull(accuracy_m, 0)))        AS accuracy_m_sum,
    countIf(accuracy_m IS NOT NULL)              AS accuracy_samples
FROM hailpath.rider_positions
GROUP BY hour, zone_id;


-- Reading them:
--
--   distinct drivers seen in a zone   uniqMerge(drivers)
--   mean speed                        sum(speed_kmh_sum) / sum(speed_samples)
--   share of time on a trip           sum(on_trip_positions) / sum(positions)
--   mean phone accuracy               sum(accuracy_m_sum) / sum(accuracy_samples)
