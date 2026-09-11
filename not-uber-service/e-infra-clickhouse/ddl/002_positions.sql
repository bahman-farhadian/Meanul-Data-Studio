-- Where drivers and riders were, over time.
--
-- Two tables per stream, which is the normal ClickHouse pattern:
--   *_local  is the real table, stored on the node and copied to its replica;
--   the plain name is a Distributed table - it stores nothing itself and
--   simply fans a query out to both shards and collects the answers.
-- Services and dashboards always use the plain name.

CREATE TABLE IF NOT EXISTS nus.driver_positions_local ON CLUSTER nus_cluster
(
    -- Ids are FixedString, not String: every one of these is a fixed-width
    -- format by construction (see z-lib/nus-common/nus_common/ids.py, the
    -- one place that mints them), so a variable-length column with its own
    -- length prefix would only cost more to store and compare for no
    -- benefit. A row that does not fit this width is a bug upstream, not
    -- something the warehouse should quietly accept.
    driver_id     FixedString(10),
    trip_id       Nullable(FixedString(21)),
    -- Enum, not LowCardinality(String): status is a closed set already
    -- enforced by the Avro schema (DriverStatus) and by Postgres's own
    -- CHECK constraint. Enum8 keeps that same guarantee here - a value
    -- outside this list is a write-time error, not a silently accepted
    -- new category threading through the dictionary.
    status        Enum8('offline' = 1, 'idle' = 2, 'en_route_pickup' = 3, 'on_trip' = 4),
    lat           Float64,
    lon           Float64,
    heading_deg   Nullable(Float32),
    speed_kmh     Nullable(Float32),
    -- Still LowCardinality, not Enum: the 263 NYC TLC taxi zones are real
    -- external data (city_zones, restored from TLC's own dataset - see
    -- h-bootstrap/lion-prepare/taxi-zones.sql), not a closed set fixed at
    -- schema time. The dictionary encoding is what buys the compression
    -- here; FixedString underneath makes the dictionary's own entries
    -- fixed-width too.
    zone_id       LowCardinality(FixedString(7)),
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
    -- rider_id is a passenger id (psg-NNNNNN) - see driver_positions above
    -- for why FixedString, not String.
    rider_id      FixedString(10),
    trip_id       Nullable(FixedString(21)),
    lat           Float64,
    lon           Float64,
    accuracy_m    Nullable(Float32),
    zone_id       LowCardinality(FixedString(7)),
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
