-- The two rollups 005_rollups.sql's own comment named as needing
-- AggregatingMergeTree and never built: a real percentile, and a real
-- distinct count. Neither is a plain additive sum - averaging a set of
-- per-node p95s would not be the true p95, and adding two nodes' distinct
-- counts together would double-count anyone seen on both - which is
-- exactly the class of metric AggregateFunction/*State/*Merge exists for.

-- 1. What the real p95 trip time in a zone is right now, not just the
-- average trip_stats_hourly's sums already answer.
CREATE TABLE IF NOT EXISTS nus.trip_duration_percentiles_hourly_local ON CLUSTER nus_cluster
(
    hour           DateTime('UTC'),
    pickup_zone_id LowCardinality(String),
    -- Nullable(UInt32), not UInt32: actual_duration_s is itself Nullable
    -- (trip_events_local) and quantileState() over a Nullable column
    -- produces a Nullable-typed state - confirmed directly, ClickHouse
    -- refuses to insert one into a plain UInt32 state column.
    p50_state      AggregateFunction(quantile(0.5), Nullable(UInt32)),
    p95_state      AggregateFunction(quantile(0.95), Nullable(UInt32))
)
ENGINE = ReplicatedAggregatingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, pickup_zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.trip_duration_percentiles_hourly_mv ON CLUSTER nus_cluster
TO nus.trip_duration_percentiles_hourly_local
AS
SELECT
    toStartOfHour(event_time)          AS hour,
    pickup_zone_id,
    quantileState(0.5)(actual_duration_s)  AS p50_state,
    quantileState(0.95)(actual_duration_s) AS p95_state
FROM nus.trip_events_local
-- Only a completed trip has a real actual_duration_s - quantileState()
-- over a Nullable column ignores NULLs on its own, but the same filter
-- 005_rollups.sql already uses keeps this from aggregating rows that can
-- only ever contribute nothing.
WHERE status = 'completed'
GROUP BY hour, pickup_zone_id;

CREATE TABLE IF NOT EXISTS nus.trip_duration_percentiles_hourly ON CLUSTER nus_cluster
AS nus.trip_duration_percentiles_hourly_local
ENGINE = Distributed(nus_cluster, nus, trip_duration_percentiles_hourly_local, cityHash64(pickup_zone_id));

-- Query with: SELECT hour, quantileMerge(0.95)(p95_state) FROM
-- trip_duration_percentiles_hourly GROUP BY hour - never read p95_state
-- directly, it is an intermediate state, not a number.

-- 2. How many distinct drivers/riders were active this hour - unanswerable
-- from trip_events (a trip touches one driver and one rider each, not the
-- whole fleet) or from a plain count (the same driver reports a position
-- every few seconds, so count() would just measure tick frequency).
--
-- Two materialized views feed one table, one per source stream - a
-- standard AggregatingMergeTree pattern, confirmed directly: each MV
-- writes only the column its own stream can populate, and merging two
-- rows for the same hour combines their states correctly (a column a
-- given MV never touched simply contributes nothing to that merge, not
-- a wrong answer) - verified live before writing this to disk.
CREATE TABLE IF NOT EXISTS nus.active_entities_hourly_local ON CLUSTER nus_cluster
(
    hour         DateTime('UTC'),
    driver_state AggregateFunction(uniq, FixedString(10)),
    rider_state  AggregateFunction(uniq, FixedString(10))
)
ENGINE = ReplicatedAggregatingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(hour)
ORDER BY hour;

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.active_drivers_hourly_mv ON CLUSTER nus_cluster
TO nus.active_entities_hourly_local
AS
SELECT
    toStartOfHour(event_time) AS hour,
    uniqState(driver_id)      AS driver_state
FROM nus.driver_positions_local
GROUP BY hour;

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.active_riders_hourly_mv ON CLUSTER nus_cluster
TO nus.active_entities_hourly_local
AS
SELECT
    toStartOfHour(event_time) AS hour,
    uniqState(rider_id)       AS rider_state
FROM nus.rider_positions_local
GROUP BY hour;

CREATE TABLE IF NOT EXISTS nus.active_entities_hourly ON CLUSTER nus_cluster
AS nus.active_entities_hourly_local
ENGINE = Distributed(nus_cluster, nus, active_entities_hourly_local, cityHash64(hour));

-- Query with: SELECT hour, uniqMerge(driver_state) AS drivers,
-- uniqMerge(rider_state) AS riders FROM active_entities_hourly GROUP BY hour.
