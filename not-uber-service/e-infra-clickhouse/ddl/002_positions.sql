-- Where drivers and riders were, over time.
--
-- Two tables per stream, which is the normal ClickHouse pattern:
--   *_local  is the real table, stored on the node and copied to its replica;
--   the plain name is a Distributed table - it stores nothing itself and
--   simply fans a query out to both shards and collects the answers.
-- Services and dashboards always use the plain name.

CREATE TABLE IF NOT EXISTS nus.driver_positions_local ON CLUSTER nus_cluster
(
    driver_id     String,
    trip_id       Nullable(String),
    status        LowCardinality(String),
    lat           Float64,
    lon           Float64,
    heading_deg   Nullable(Float32),
    speed_kmh     Nullable(Float32),
    zone_id       LowCardinality(String),
    event_time    DateTime64(3, 'UTC'),
    -- Computed on write and used for partitioning, so queries by day never
    -- have to look at months of data.
    event_date    Date MATERIALIZED toDate(event_time)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(event_date)
ORDER BY (driver_id, event_time)
-- Position history is huge and loses value quickly. Three months is plenty
-- for the dashboards; the trip record keeps what matters for longer.
TTL event_date + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS nus.driver_positions ON CLUSTER nus_cluster
AS nus.driver_positions_local
-- Split by driver, so everything about one driver sits on one shard and a
-- per-driver query touches half the cluster instead of all of it.
ENGINE = Distributed(nus_cluster, nus, driver_positions_local, cityHash64(driver_id));

CREATE TABLE IF NOT EXISTS nus.rider_positions_local ON CLUSTER nus_cluster
(
    rider_id      String,
    trip_id       Nullable(String),
    lat           Float64,
    lon           Float64,
    accuracy_m    Nullable(Float32),
    zone_id       LowCardinality(String),
    event_time    DateTime64(3, 'UTC'),
    event_date    Date MATERIALIZED toDate(event_time)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(event_date)
ORDER BY (rider_id, event_time)
TTL event_date + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS nus.rider_positions ON CLUSTER nus_cluster
AS nus.rider_positions_local
ENGINE = Distributed(nus_cluster, nus, rider_positions_local, cityHash64(rider_id));
