-- The demand score of each city zone over time, as published by city-service.
-- Redis holds only the current value (with a six-hour lifetime); this is the
-- history behind it, which is what makes "when is this zone busy" answerable.

CREATE TABLE IF NOT EXISTS nus.hotspot_history_local ON CLUSTER nus_cluster
(
    -- LowCardinality(FixedString)/Enum8 - same reasoning as 002_positions.sql
    -- and 003_trips.sql: zone_id is a fixed-width dictionary of a
    -- config-sized set; period is a closed set already enforced by the
    -- Avro schema (DayPeriod) and by Postgres's own segment_traffic
    -- CHECK constraint.
    zone_id            LowCardinality(FixedString(7)),
    period             Enum8('night' = 1, 'morning' = 2, 'afternoon' = 3, 'evening' = 4),
    demand_score       Float64,
    open_requests      UInt32,
    available_drivers  UInt32,
    surge_multiplier   Float64,
    computed_at        DateTime64(3, 'UTC'),
    event_date         Date MATERIALIZED toDate(computed_at)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(event_date)
ORDER BY (zone_id, computed_at)
TTL event_date + INTERVAL 365 DAY;

CREATE TABLE IF NOT EXISTS nus.hotspot_history ON CLUSTER nus_cluster
AS nus.hotspot_history_local
ENGINE = Distributed(nus_cluster, nus, hotspot_history_local, cityHash64(zone_id));
