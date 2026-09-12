-- How much of a driver's online time is actually spent on a trip.
--
-- Not sourced from driver_sessions (Postgres): that table has no CDC path
-- into ClickHouse today (clickhouse-sink only consumes the Kafka topics
-- listed in its own TOPICS constant, none of which are cdc.* - Debezium's
-- CDC topics feed Redis, through cache-updater, not the warehouse).
-- driver_positions_local already reports every driver's real status every
-- few seconds, which is a finer-grained utilization signal than session
-- start/end would be anyway - online_ticks vs busy_ticks per hour, not
-- just online-vs-offline per shift.

CREATE TABLE IF NOT EXISTS nus.driver_utilization_hourly_local ON CLUSTER nus_cluster
(
    hour          DateTime('UTC'),
    driver_id     FixedString(11),
    -- Ticks where the driver reported anything but offline - the
    -- denominator for "what share of online time was spent on a trip".
    online_ticks  UInt64,
    -- Ticks where the driver reported on_trip specifically.
    busy_ticks    UInt64
)
ENGINE = ReplicatedSummingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, driver_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.driver_utilization_hourly_mv ON CLUSTER nus_cluster
TO nus.driver_utilization_hourly_local
AS
SELECT
    toStartOfHour(event_time)         AS hour,
    driver_id,
    countIf(status != 'offline')      AS online_ticks,
    countIf(status = 'on_trip')       AS busy_ticks
FROM nus.driver_positions_local
GROUP BY hour, driver_id;

CREATE TABLE IF NOT EXISTS nus.driver_utilization_hourly ON CLUSTER nus_cluster
AS nus.driver_utilization_hourly_local
ENGINE = Distributed(nus_cluster, nus, driver_utilization_hourly_local, cityHash64(driver_id));

-- Query with: SELECT hour, driver_id, sum(busy_ticks) / sum(online_ticks)
-- AS utilization FROM driver_utilization_hourly GROUP BY hour, driver_id
-- HAVING sum(online_ticks) > 0 - the division belongs at query time, same
-- rule as every other SummingMergeTree rollup here (never read a raw
-- ratio column, it would average wrong across shards).
--
-- An entirely-offline driver-hour never appears here at all - confirmed
-- live, not a bug: SummingMergeTree drops a row once every summable
-- column merges to zero (online_ticks=0 and busy_ticks=0 together), which
-- is documented behavior, not something this schema opts into separately.
-- Semantically correct for this table anyway - a driver who reported
-- nothing but offline that hour was not part of the active fleet, so
-- there is genuinely nothing to say about their utilization.
