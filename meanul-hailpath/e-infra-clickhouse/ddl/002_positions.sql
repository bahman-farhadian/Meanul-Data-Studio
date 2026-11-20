-- Driver and rider positions. Idle GPS does not belong in MergeTree.
-- Grafana last-known map reads hailpath.driver_last_known.

CREATE TABLE IF NOT EXISTS hailpath.driver_last_known
(
    driver_id     UInt32,
    trip_id       Nullable(UInt64),
    status        Enum8('offline' = 1, 'idle' = 2, 'en_route_pickup' = 3, 'on_trip' = 4),
    lat           Float64,
    lon           Float64,
    heading_deg   Nullable(Float32),
    speed_kmh     Nullable(Float32),
    zone_id       UInt16,
    event_time    DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(event_time)
ORDER BY driver_id;


CREATE TABLE IF NOT EXISTS hailpath.driver_positions
(
    driver_id     UInt32,
    trip_id       UInt64,
    status        Enum8('offline' = 1, 'idle' = 2, 'en_route_pickup' = 3, 'on_trip' = 4),
    lat           Float64,
    lon           Float64,
    heading_deg   Nullable(Float32),
    speed_kmh     Nullable(Float32),
    zone_id       UInt16,
    event_time    DateTime64(3, 'UTC'),
    event_date    Date MATERIALIZED toDate(event_time)
)
ENGINE = MergeTree
PARTITION BY event_date
ORDER BY (driver_id, event_time)
TTL event_date + INTERVAL 2 DAY;


CREATE TABLE IF NOT EXISTS hailpath.rider_positions
(
    rider_id      UInt32,
    trip_id       UInt64,
    lat           Float64,
    lon           Float64,
    accuracy_m    Nullable(Float32),
    zone_id       UInt16,
    event_time    DateTime64(3, 'UTC'),
    event_date    Date MATERIALIZED toDate(event_time)
)
ENGINE = MergeTree
PARTITION BY event_date
ORDER BY (rider_id, event_time)
TTL event_date + INTERVAL 2 DAY;
