-- An hourly summary of completed trips, kept up to date automatically.
--
-- A materialized view here is not a saved query: it is a trigger. Every time
-- rows land in trip_events_local, the view aggregates the new rows and adds
-- the result to the summary table. Dashboards then read a small table
-- instead of scanning every trip event.
--
-- One thing to know when reading it: the summary is filled per node, and the
-- same hour and zone can therefore exist on both shards. ALWAYS aggregate
-- when querying it (sum(), and divide afterwards) - never read a single row
-- and treat it as the total.

CREATE TABLE IF NOT EXISTS nus.trip_stats_hourly_local ON CLUSTER nus_cluster
(
    hour             DateTime('UTC'),
    pickup_zone_id   LowCardinality(String),
    completed_trips  UInt64,
    revenue          Float64,
    -- Surge added up, not averaged: an average of averages would be wrong.
    -- Divide by completed_trips at query time to get the real average.
    surge_sum        Float64,
    route_km_total   Float64,
    overrun_trips    UInt64
)
-- SummingMergeTree adds up rows that share the sort key as it merges parts,
-- so the table stays small on its own.
ENGINE = ReplicatedSummingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, pickup_zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.trip_stats_hourly_mv ON CLUSTER nus_cluster
TO nus.trip_stats_hourly_local
AS
SELECT
    toStartOfHour(event_time)                     AS hour,
    pickup_zone_id,
    count()                                       AS completed_trips,
    sum(ifNull(fare_final, 0))                    AS revenue,
    sum(ifNull(surge_multiplier, 1))              AS surge_sum,
    sum(ifNull(route_km, 0))                      AS route_km_total,
    countIf(took_longer_than_predicted = 1)       AS overrun_trips
FROM nus.trip_events_local
-- Only finished trips carry a final fare, so only they belong in a revenue
-- summary.
WHERE status = 'completed'
GROUP BY hour, pickup_zone_id;

CREATE TABLE IF NOT EXISTS nus.trip_stats_hourly ON CLUSTER nus_cluster
AS nus.trip_stats_hourly_local
ENGINE = Distributed(nus_cluster, nus, trip_stats_hourly_local, cityHash64(pickup_zone_id));
