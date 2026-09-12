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
    driver_id     FixedString(11),
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
    -- LowCardinality(String), not FixedString: the 263 NYC TLC taxi zones
    -- are real external data (city_zones, restored from TLC's own dataset -
    -- see h-bootstrap/lion-prepare/taxi-zones.sql), and TLC's own
    -- LocationID is a variable-width 1-3 digit id ("1".."263"), not a
    -- fixed-width format this codebase controls the way driver_id/trip_id
    -- are (see nus_common/ids.py) - String is the honest type for an id
    -- minted by someone else. LowCardinality still buys the same dictionary
    -- compression a config-sized set like this wants.
    zone_id       LowCardinality(String),
    event_time    DateTime64(3, 'UTC'),
    -- Computed on write and used for partitioning, so queries by day never
    -- have to look at months of data.
    event_date    Date MATERIALIZED toDate(event_time)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(event_date)
ORDER BY (driver_id, event_time)
-- Position history is huge and loses value quickly - the trip record
-- keeps what matters for longer (trip_events, 365 days). Three months
-- was the original call, before real fleet scale (~106,000 drivers) was
-- actually flowing: at that volume this table alone is on the order of
-- 15-20GB/day per ClickHouse node, and 90 days of it would be several
-- times this host's entire data disk. Three days is what real-time
-- driver-side debugging actually needs (Grafana's own live view reads
-- Redis, not this) and fits real disk with room to spare - re-check
-- against actual observed growth after a day of real traffic rather
-- than trusting this estimate forever.
TTL event_date + INTERVAL 3 DAY;

CREATE TABLE IF NOT EXISTS nus.driver_positions ON CLUSTER nus_cluster
AS nus.driver_positions_local
-- Split by driver, so everything about one driver sits on one shard and a
-- per-driver query touches half the cluster instead of all of it.
ENGINE = Distributed(nus_cluster, nus, driver_positions_local, cityHash64(driver_id));

CREATE TABLE IF NOT EXISTS nus.rider_positions_local ON CLUSTER nus_cluster
(
    -- rider_id is a passenger id (psg-NNNNNN) - see driver_positions above
    -- for why FixedString, not String.
    rider_id      FixedString(11),
    trip_id       Nullable(FixedString(21)),
    lat           Float64,
    lon           Float64,
    accuracy_m    Nullable(Float32),
    -- LowCardinality(String), not FixedString - see driver_positions above.
    zone_id       LowCardinality(String),
    event_time    DateTime64(3, 'UTC'),
    event_date    Date MATERIALIZED toDate(event_time)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(event_date)
ORDER BY (rider_id, event_time)
-- Only a travelling rider reports at all (passenger-service), and only
-- for the length of one trip - real volume here is tiny next to
-- driver_positions regardless of fleet size, so this can afford to keep
-- more history for the same reason driver_positions can't; still cut
-- down from 90 days for consistency with its paired table above.
TTL event_date + INTERVAL 7 DAY;

CREATE TABLE IF NOT EXISTS nus.rider_positions ON CLUSTER nus_cluster
AS nus.rider_positions_local
ENGINE = Distributed(nus_cluster, nus, rider_positions_local, cityHash64(rider_id));
