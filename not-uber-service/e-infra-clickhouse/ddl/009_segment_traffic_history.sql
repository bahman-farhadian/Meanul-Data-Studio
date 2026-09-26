-- Every congestion-factor update city-service applies to a zone's road
-- segments, as it happens. segment_traffic (Postgres) only ever holds the
-- current factor; this is the history behind it, which is what makes a real
-- time series of "how congested was this zone, at this time" answerable -
-- and, once routing picks between multiple real routes (nus_common.routing),
-- a real signal of how traffic actually shifted between them.

CREATE TABLE IF NOT EXISTS nus.segment_traffic_history_local ON CLUSTER nus_cluster
(
    -- Same reasoning as 004_hotspots.sql: zone_id is TLC's own LocationID,
    -- period is the closed DayPeriod set shared with the Avro schema.
    -- The message's own identity, from the Avro envelope every producer
    -- stamps. UUID is 16 bytes against 36 for the text form, and the sink
    -- refuses an id it has already written: ClickHouse does not deduplicate
    -- on its own - ReplacingMergeTree only collapses eventually, only within
    -- a partition, and only on merge - so uniqueness has to hold before the
    -- warehouse or no count here can be trusted.
    event_id           UUID,
    zone_id            LowCardinality(String),
    period             Enum8('night' = 1, 'morning' = 2, 'afternoon' = 3, 'evening' = 4),
    congestion_factor  Float64,
    speed_samples      UInt32,
    segments_updated   UInt32,
    computed_at        DateTime64(3, 'UTC'),
    event_date         Date MATERIALIZED toDate(computed_at)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(event_date)
ORDER BY (zone_id, computed_at)
-- Ninety days, not a year. Measured rather than chosen: at full scale the
-- four 365-day tables together projected 32.5 GiB per node against a
-- 96 GiB quota, and this project's own full-scale scope is SEVEN DAYS of
-- history - a year of retention provisions for fifty-two times more data
-- than will ever exist here. Ninety days is still twelve times the scope
-- and supports every quarterly trend a dashboard asks for. Re-measure with
-- make capacity rather than trusting this number forever.
TTL event_date + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS nus.segment_traffic_history ON CLUSTER nus_cluster
AS nus.segment_traffic_history_local
ENGINE = Distributed(nus_cluster, nus, segment_traffic_history_local, cityHash64(zone_id));
