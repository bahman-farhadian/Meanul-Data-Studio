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
TTL event_date + INTERVAL 365 DAY;

CREATE TABLE IF NOT EXISTS nus.segment_traffic_history ON CLUSTER nus_cluster
AS nus.segment_traffic_history_local
ENGINE = Distributed(nus_cluster, nus, segment_traffic_history_local, cityHash64(zone_id));
