-- Daily rollups - the grain Superset's trend charts actually want.
-- trip_stats_hourly answers "what happened this hour"; a dashboard asking
-- "how is this month trending" would otherwise have to sum 720+ hourly
-- rows itself, every time it renders.

-- 1. trip_stats_daily chains off trip_stats_hourly_local, not
-- trip_events_local directly: summing already-hourly sums into a daily
-- sum is exactly as correct as summing the raw events (addition
-- associates), and it is one order of magnitude less data for the
-- trigger to scan. A materialized view sourcing from another materialized
-- view's own target table is a normal, supported ClickHouse pattern - the
-- second MV just watches trip_stats_hourly_local's inserts instead of
-- trip_events_local's.
CREATE TABLE IF NOT EXISTS nus.trip_stats_daily_local ON CLUSTER nus_cluster
(
    day              Date,
    pickup_zone_id   LowCardinality(String),
    completed_trips  UInt64,
    revenue          Float64,
    surge_sum        Float64,
    route_km_total   Float64,
    overrun_trips    UInt64
)
ENGINE = ReplicatedSummingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(day)
ORDER BY (day, pickup_zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.trip_stats_daily_mv ON CLUSTER nus_cluster
TO nus.trip_stats_daily_local
AS
SELECT
    toDate(hour)             AS day,
    pickup_zone_id,
    sum(completed_trips)     AS completed_trips,
    sum(revenue)              AS revenue,
    sum(surge_sum)            AS surge_sum,
    sum(route_km_total)       AS route_km_total,
    sum(overrun_trips)        AS overrun_trips
FROM nus.trip_stats_hourly_local
GROUP BY day, pickup_zone_id;

CREATE TABLE IF NOT EXISTS nus.trip_stats_daily ON CLUSTER nus_cluster
AS nus.trip_stats_daily_local
ENGINE = Distributed(nus_cluster, nus, trip_stats_daily_local, cityHash64(pickup_zone_id));

-- 2. od_matrix_daily - pickup x dropoff, the real "where does demand from
-- this zone actually go" question, and the thing to compare against
-- od_pair_calibration's real TLC shares. Must source from
-- trip_events_local directly: dropoff_zone_id lives there, not in the
-- hourly rollup above.
CREATE TABLE IF NOT EXISTS nus.od_matrix_daily_local ON CLUSTER nus_cluster
(
    day               Date,
    pickup_zone_id    LowCardinality(String),
    dropoff_zone_id   LowCardinality(String),
    completed_trips   UInt64,
    revenue           Float64,
    route_km_total    Float64
)
ENGINE = ReplicatedSummingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(day)
ORDER BY (day, pickup_zone_id, dropoff_zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.od_matrix_daily_mv ON CLUSTER nus_cluster
TO nus.od_matrix_daily_local
AS
SELECT
    toDate(event_time)          AS day,
    pickup_zone_id,
    dropoff_zone_id,
    count()                     AS completed_trips,
    sum(ifNull(fare_final, 0))  AS revenue,
    sum(ifNull(route_km, 0))    AS route_km_total
FROM nus.trip_events_local
WHERE status = 'completed'
GROUP BY day, pickup_zone_id, dropoff_zone_id;

CREATE TABLE IF NOT EXISTS nus.od_matrix_daily ON CLUSTER nus_cluster
AS nus.od_matrix_daily_local
ENGINE = Distributed(nus_cluster, nus, od_matrix_daily_local, cityHash64(pickup_zone_id));
