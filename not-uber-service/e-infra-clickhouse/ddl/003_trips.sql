-- Every status change of every trip, already enriched by clickhouse-sink.
--
-- The sink adds the columns the raw Kafka message does not carry: the demand
-- score of the pickup zone at that moment, whether the trip counts as a
-- hotspot trip, and how far the real duration drifted from the predicted one.
-- Those lookups come from Redis, never from the OLTP database.

CREATE TABLE IF NOT EXISTS nus.trip_events_local ON CLUSTER nus_cluster
(
    -- FixedString/Enum8 for the ids and status this codebase mints itself
    -- (nus_common/ids.py) and constrains itself (Avro's TripStatus, and
    -- Postgres's own CHECK) - see driver_positions/rider_positions in
    -- 002_positions.sql. pickup_zone_id is LowCardinality(String), not
    -- FixedString: it's TLC's own variable-width LocationID, not a format
    -- this codebase controls.
    trip_id                    FixedString(21),
    rider_id                   FixedString(11),
    driver_id                  Nullable(FixedString(11)),
    status                     Enum8(
                                   'requested' = 1, 'matched' = 2, 'accepted' = 3,
                                   'en_route_pickup' = 4, 'in_progress' = 5, 'completed' = 6,
                                   'cancelled_by_passenger' = 7, 'cancelled_by_driver' = 8,
                                   'no_driver_found' = 9
                               ),
    pickup_zone_id             LowCardinality(String),
    dropoff_zone_id            LowCardinality(String),

    route_km                   Nullable(Float64),
    predicted_duration_s       Nullable(UInt32),
    actual_duration_s          Nullable(UInt32),
    -- actual minus predicted. Positive means the trip took longer than the
    -- route calculation promised.
    duration_delta_s           Nullable(Int32),
    took_longer_than_predicted Nullable(UInt8),

    surge_multiplier           Nullable(Float64),
    hotspot_score              Nullable(Float64),
    is_hotspot_trip            Nullable(UInt8),
    fare_estimate              Nullable(Float64),
    fare_final                 Nullable(Float64),

    event_time                 DateTime64(3, 'UTC'),
    event_date                 Date MATERIALIZED toDate(event_time)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(event_date)
ORDER BY (trip_id, event_time)
-- Trips are the business record, so they are kept far longer than positions.
TTL event_date + INTERVAL 365 DAY;

CREATE TABLE IF NOT EXISTS nus.trip_events ON CLUSTER nus_cluster
AS nus.trip_events_local
-- Split by trip, so the whole story of one trip lands on one shard.
ENGINE = Distributed(nus_cluster, nus, trip_events_local, cityHash64(trip_id));
