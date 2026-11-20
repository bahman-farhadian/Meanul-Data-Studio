# Data dictionary — version 1

Generated from the shipped migrations, ClickHouse DDL, and
`c-infra-kafka/topics/topics.tsv`. A live database is not consulted.
If a source and this file disagree, the source wins. Regenerate
with `python3 z-docs/schema_inventory.py` from `meanul-hailpath/`.

`ways` and `ways_vertices_pgr` are built by
`h-bootstrap/lion-prepare/build-graph.sql`, not by a migration,
so they are not listed here.

Version 1 stores a payment as two columns on `trips`, `payment_method` and `driver_payout`. A failed or refunded charge cannot be represented. An append-only payments record is not in version 1.

## OLTP

Produced by `h-bootstrap` and the live services. Consumed by the
services, Debezium, and the archiver.

### `drivers`

| Column | Type |
| --- | --- |
| `driver_id` | integer PRIMARY KEY |
| `full_name` | text NOT NULL |
| `phone` | text |
| `rating` | numeric(2,1) NOT NULL DEFAULT 5.0 |
| `status` | text NOT NULL DEFAULT 'offline' |
| `home_zone_id` | smallint |
| `last_lat` | double precision |
| `last_lon` | double precision |
| `last_seen_at` | timestamptz |
| `device` | jsonb NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | timestamptz NOT NULL DEFAULT now() |
| `updated_at` | timestamptz NOT NULL DEFAULT now() |

### `passengers`

| Column | Type |
| --- | --- |
| `passenger_id` | integer PRIMARY KEY |
| `full_name` | text NOT NULL |
| `phone` | text |
| `rating` | numeric(2,1) NOT NULL DEFAULT 5.0 |
| `home_zone_id` | smallint |
| `preferences` | jsonb NOT NULL DEFAULT '{}'::jsonb |
| `device` | jsonb NOT NULL DEFAULT '{}'::jsonb |
| `created_at` | timestamptz NOT NULL DEFAULT now() |
| `updated_at` | timestamptz NOT NULL DEFAULT now() |

### `trips`

| Column | Type |
| --- | --- |
| `trip_id` | bigint PRIMARY KEY |
| `rider_id` | integer NOT NULL REFERENCES passengers(passenger_id) |
| `driver_id` | integer REFERENCES drivers(driver_id) |
| `status` | text NOT NULL |
| `pickup_point` | geometry(Point, 4326) NOT NULL |
| `dropoff_point` | geometry(Point, 4326) NOT NULL |
| `pickup_zone_id` | smallint |
| `dropoff_zone_id` | smallint |
| `route` | geometry(Geometry, 4326) |
| `route_km` | double precision |
| `predicted_duration_s` | integer |
| `actual_duration_s` | integer |
| `surge_multiplier` | numeric(4,2) |
| `fare_estimate` | numeric(10,2) |
| `fare_final` | numeric(10,2) |
| `attributes` | jsonb NOT NULL DEFAULT '{}'::jsonb |
| `requested_at` | timestamptz NOT NULL |
| `matched_at` | timestamptz |
| `accepted_at` | timestamptz |
| `started_at` | timestamptz |
| `ended_at` | timestamptz |
| `created_at` | timestamptz NOT NULL DEFAULT now() |
| `updated_at` | timestamptz NOT NULL DEFAULT now() |
| `requested_vehicle_type` | text NOT NULL DEFAULT 'economy' |
| `cancellation_reason` | text |
| `driver_payout` | numeric(10, 2) |
| `payment_method` | text |
| `arrived_at` | timestamptz |
| `passenger_count` | smallint NOT NULL DEFAULT 1 |

### `city_zones`

| Column | Type |
| --- | --- |
| `zone_id` | smallint PRIMARY KEY |
| `name` | text NOT NULL |
| `borough` | text NOT NULL |
| `boundary` | geometry(MultiPolygon, 4326) NOT NULL |
| `centroid` | geometry(Point, 4326) NOT NULL |
| `servicable` | boolean NOT NULL DEFAULT true |
| `created_at` | timestamptz NOT NULL DEFAULT now() |

### `segment_traffic`

| Column | Type |
| --- | --- |
| `way_id` | bigint NOT NULL |
| `period` | text NOT NULL |
| `congestion_factor` | double precision NOT NULL DEFAULT 1.0 |
| `sample_count` | integer NOT NULL DEFAULT 0 |
| `updated_at` | timestamptz NOT NULL DEFAULT now() |

### `vehicles`

| Column | Type |
| --- | --- |
| `vehicle_id` | integer PRIMARY KEY |
| `driver_id` | integer NOT NULL REFERENCES drivers(driver_id) |
| `vehicle_type` | text NOT NULL DEFAULT 'economy' |
| `seats` | integer NOT NULL DEFAULT 4 |
| `make` | text NOT NULL |
| `model` | text NOT NULL |
| `year` | integer |
| `plate` | text |
| `colour` | text |
| `created_at` | timestamptz NOT NULL DEFAULT now() |

### `trip_ratings`

| Column | Type |
| --- | --- |
| `rating_id` | bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY |
| `trip_id` | bigint NOT NULL REFERENCES trips(trip_id) |
| `rater_type` | text NOT NULL |
| `rating` | smallint NOT NULL |
| `created_at` | timestamptz NOT NULL DEFAULT now() |

### `driver_sessions`

| Column | Type |
| --- | --- |
| `session_id` | bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY |
| `driver_id` | integer NOT NULL REFERENCES drivers(driver_id) |
| `started_at` | timestamptz NOT NULL |
| `ended_at` | timestamptz |
| `zone_id` | smallint NOT NULL |

### `zone_demand_calibration`

| Column | Type |
| --- | --- |
| `zone_id` | smallint NOT NULL REFERENCES city_zones(zone_id) |
| `hour_of_day` | smallint NOT NULL |
| `day_of_week` | smallint NOT NULL |
| `weight` | numeric(8, 4) NOT NULL |
| `source_month` | date |
| `calibrated_at` | timestamptz NOT NULL DEFAULT now() |

### `od_pair_calibration`

| Column | Type |
| --- | --- |
| `pickup_zone_id` | smallint NOT NULL REFERENCES city_zones(zone_id) |
| `dropoff_zone_id` | smallint NOT NULL REFERENCES city_zones(zone_id) |
| `trip_share` | numeric(8, 6) NOT NULL |
| `avg_fare` | numeric(10, 2) |
| `avg_duration_s` | integer |
| `source_month` | date |
| `calibrated_at` | timestamptz NOT NULL DEFAULT now() |

### `dispatch_offers`

| Column | Type |
| --- | --- |
| `offer_id` | bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY |
| `trip_id` | bigint NOT NULL REFERENCES trips(trip_id) |
| `driver_id` | integer NOT NULL REFERENCES drivers(driver_id) |
| `sequence` | smallint NOT NULL |
| `offered_at` | timestamptz NOT NULL |
| `expires_at` | timestamptz NOT NULL |
| `responded_at` | timestamptz |
| `status` | text NOT NULL |
| `eta_seconds` | integer |
| `distance_to_pickup_m` | integer |

## Kafka topics

Produced by the named service. Consumed by ksqlDB and
`n-service-clickhouse-sink`. `cdc.*` topics are created by Debezium
and are not in `topics.tsv`.

| Topic | Partitions | Retention hours | Key | Purpose |
| --- | --- | --- | --- | --- |
| `driver_location` | 12 | 6 | driver_id | where each driver is, every few seconds |
| `rider_location` | 12 | 12 | rider_id | where each rider's phone is during a trip |
| `trip_requests` | 6 | - | trip_id | a passenger asks for a ride |
| `trip_lifecycle` | 6 | - | trip_id | every status change of a trip, up to the final fare |
| `dispatch_offers` | 6 | - | trip_id | every offer dispatch made, and what the driver said |
| `city_hotspots` | 3 | - | zone_id | demand score per city zone, published by city-service |
| `segment_traffic_updates` | 3 | - | zone_id | a real congestion-factor update, published by city-service |

## Warehouse

These are the tables services and dashboards query. Each one is
stored on the one ClickHouse. A materialized view is a trigger,
not a table, and is not listed.

### `hailpath.driver_last_known`

| Column | Type |
| --- | --- |
| `driver_id` | UInt32 |
| `trip_id` | Nullable(UInt64) |
| `status` | Enum8('offline' = 1, 'idle' = 2, 'en_route_pickup' = 3, 'on_trip' = 4) |
| `lat` | Float64 |
| `lon` | Float64 |
| `heading_deg` | Nullable(Float32) |
| `speed_kmh` | Nullable(Float32) |
| `zone_id` | UInt16 |
| `event_time` | DateTime64(3, 'UTC') |

### `hailpath.driver_positions`

| Column | Type |
| --- | --- |
| `driver_id` | UInt32 |
| `trip_id` | UInt64 |
| `status` | Enum8('offline' = 1, 'idle' = 2, 'en_route_pickup' = 3, 'on_trip' = 4) |
| `lat` | Float64 |
| `lon` | Float64 |
| `heading_deg` | Nullable(Float32) |
| `speed_kmh` | Nullable(Float32) |
| `zone_id` | UInt16 |
| `event_time` | DateTime64(3, 'UTC') |
| `event_date` | Date MATERIALIZED toDate(event_time) |

### `hailpath.rider_positions`

| Column | Type |
| --- | --- |
| `rider_id` | UInt32 |
| `trip_id` | UInt64 |
| `lat` | Float64 |
| `lon` | Float64 |
| `accuracy_m` | Nullable(Float32) |
| `zone_id` | UInt16 |
| `event_time` | DateTime64(3, 'UTC') |
| `event_date` | Date MATERIALIZED toDate(event_time) |

### `hailpath.trip_events`

| Column | Type |
| --- | --- |
| `event_id` | UUID |
| `trip_id` | UInt64 |
| `rider_id` | UInt32 |
| `driver_id` | Nullable(UInt32) |
| `status` | Enum8( 'requested' = 1, 'matched' = 2, 'accepted' = 3, 'en_route_pickup' = 4, 'in_progress' = 5, 'completed' = 6, 'cancelled_by_passenger' = 7, 'cancelled_by_driver' = 8, 'no_driver_found' = 9, 'arrived' = 10 ) |
| `pickup_zone_id` | UInt16 |
| `dropoff_zone_id` | UInt16 |
| `passenger_count` | UInt8 |
| `requested_vehicle_type` | Enum8('economy' = 1, 'xl' = 2, 'premium' = 3) |
| `route_km` | Nullable(Float64) |
| `predicted_duration_s` | Nullable(UInt32) |
| `actual_duration_s` | Nullable(UInt32) |
| `duration_delta_s` | Nullable(Int32) |
| `took_longer_than_predicted` | Nullable(UInt8) |
| `surge_multiplier` | Nullable(Float64) |
| `hotspot_score` | Nullable(Float64) |
| `is_hotspot_trip` | Nullable(UInt8) |
| `fare_estimate` | Nullable(Decimal64(2)) |
| `fare_final` | Nullable(Decimal64(2)) |
| `driver_payout` | Nullable(Decimal64(2)) |
| `payment_method` | Nullable(Enum8('card' = 1, 'wallet' = 2, 'cash' = 3)) |
| `cancellation_reason` | Nullable(Enum8( 'rider_no_show' = 1, 'driver_too_far' = 2, 'vehicle_issue' = 3, 'changed_mind' = 4, 'found_alternative' = 5, 'wait_too_long' = 6 )) |
| `requested_at` | Nullable(DateTime64(3, 'UTC')) |
| `matched_at` | Nullable(DateTime64(3, 'UTC')) |
| `accepted_at` | Nullable(DateTime64(3, 'UTC')) |
| `arrived_at` | Nullable(DateTime64(3, 'UTC')) |
| `started_at` | Nullable(DateTime64(3, 'UTC')) |
| `ended_at` | Nullable(DateTime64(3, 'UTC')) |
| `event_time` | DateTime64(3, 'UTC') |
| `event_date` | Date MATERIALIZED toDate(event_time) |

### `hailpath.trip_last_status`

| Column | Type |
| --- | --- |
| `trip_id` | UInt64 |
| `rider_id` | UInt32 |
| `driver_id` | Nullable(UInt32) |
| `status` | Enum8( 'requested' = 1, 'matched' = 2, 'accepted' = 3, 'en_route_pickup' = 4, 'in_progress' = 5, 'completed' = 6, 'cancelled_by_passenger' = 7, 'cancelled_by_driver' = 8, 'no_driver_found' = 9, 'arrived' = 10 ) |
| `pickup_zone_id` | UInt16 |
| `dropoff_zone_id` | UInt16 |
| `predicted_duration_s` | Nullable(UInt32) |
| `fare_estimate` | Nullable(Decimal64(2)) |
| `event_time` | DateTime64(3, 'UTC') |

### `hailpath.hotspot_history`

| Column | Type |
| --- | --- |
| `event_id` | UUID |
| `zone_id` | UInt16 |
| `period` | Enum8('night' = 1, 'morning' = 2, 'afternoon' = 3, 'evening' = 4) |
| `demand_score` | Float64 |
| `open_requests` | UInt32 |
| `available_drivers` | UInt32 |
| `surge_multiplier` | Float64 |
| `computed_at` | DateTime64(3, 'UTC') |
| `event_date` | Date MATERIALIZED toDate(computed_at) |

### `hailpath.trip_stats_hourly`

| Column | Type |
| --- | --- |
| `hour` | DateTime('UTC') |
| `pickup_zone_id` | UInt16 |
| `completed_trips` | UInt32 |
| `revenue` | Decimal64(2) |
| `payout_total` | Decimal64(2) |
| `surge_sum` | Float64 |
| `route_km_total` | Float64 |
| `overrun_trips` | UInt32 |

### `hailpath.trip_duration_percentiles_hourly`

| Column | Type |
| --- | --- |
| `hour` | DateTime('UTC') |
| `pickup_zone_id` | UInt16 |
| `p50_state` | AggregateFunction(quantile(0.5), Nullable(UInt32)) |
| `p95_state` | AggregateFunction(quantile(0.95), Nullable(UInt32)) |

### `hailpath.active_entities_hourly`

| Column | Type |
| --- | --- |
| `hour` | DateTime('UTC') |
| `driver_state` | AggregateFunction(uniq, UInt32) |
| `rider_state` | AggregateFunction(uniq, UInt32) |

### `hailpath.trip_stats_daily`

| Column | Type |
| --- | --- |
| `day` | Date |
| `pickup_zone_id` | UInt16 |
| `completed_trips` | UInt32 |
| `revenue` | Decimal64(2) |
| `payout_total` | Decimal64(2) |
| `surge_sum` | Float64 |
| `route_km_total` | Float64 |
| `overrun_trips` | UInt32 |

### `hailpath.od_matrix_daily`

| Column | Type |
| --- | --- |
| `day` | Date |
| `pickup_zone_id` | UInt16 |
| `dropoff_zone_id` | UInt16 |
| `completed_trips` | UInt32 |
| `revenue` | Decimal64(2) |
| `route_km_total` | Float64 |

### `hailpath.driver_utilization_hourly`

| Column | Type |
| --- | --- |
| `hour` | DateTime('UTC') |
| `driver_id` | UInt32 |
| `online_ticks` | UInt32 |
| `busy_ticks` | UInt32 |

### `hailpath.segment_traffic_history`

| Column | Type |
| --- | --- |
| `event_id` | UUID |
| `zone_id` | UInt16 |
| `period` | Enum8('night' = 1, 'morning' = 2, 'afternoon' = 3, 'evening' = 4) |
| `congestion_factor` | Float64 |
| `speed_samples` | UInt32 |
| `segments_updated` | UInt32 |
| `computed_at` | DateTime64(3, 'UTC') |
| `event_date` | Date MATERIALIZED toDate(computed_at) |

### `hailpath.dispatch_offers`

| Column | Type |
| --- | --- |
| `event_id` | UUID |
| `trip_id` | UInt64 |
| `driver_id` | UInt32 |
| `sequence` | UInt8 |
| `status` | Enum8( 'offered' = 1, 'accepted' = 2, 'declined' = 3, 'expired' = 4, 'cancelled' = 5 ) |
| `pickup_zone_id` | UInt16 |
| `eta_seconds` | Nullable(UInt32) |
| `distance_to_pickup_m` | Nullable(UInt32) |
| `surge_multiplier` | Nullable(Float64) |
| `response_s` | Nullable(UInt32) |
| `offered_at` | DateTime64(3, 'UTC') |
| `event_time` | DateTime64(3, 'UTC') |
| `event_date` | Date MATERIALIZED toDate(offered_at) |

### `hailpath.dispatch_funnel_hourly`

| Column | Type |
| --- | --- |
| `hour` | DateTime('UTC') |
| `pickup_zone_id` | UInt16 |
| `offers_made` | UInt32 |
| `offers_accepted` | UInt32 |
| `offers_declined` | UInt32 |
| `offers_expired` | UInt32 |
| `offers_cancelled` | UInt32 |
| `eta_seconds_sum` | UInt64 |
| `eta_offers` | UInt32 |
| `accepted_eta_sum` | UInt64 |
| `response_s_sum` | UInt64 |
| `responses` | UInt32 |

### `hailpath.trip_facts`

| Column | Type |
| --- | --- |
| `event_id` | UUID |
| `trip_id` | UInt64 |
| `rider_id` | UInt32 |
| `driver_id` | Nullable(UInt32) |
| `final_status` | Enum8( 'completed' = 6, 'cancelled_by_passenger' = 7, 'cancelled_by_driver' = 8, 'no_driver_found' = 9 ) |
| `pickup_zone_id` | UInt16 |
| `dropoff_zone_id` | UInt16 |
| `passenger_count` | UInt8 |
| `requested_vehicle_type` | Enum8('economy' = 1, 'xl' = 2, 'premium' = 3) |
| `requested_at` | Nullable(DateTime64(3, 'UTC')) |
| `matched_at` | Nullable(DateTime64(3, 'UTC')) |
| `accepted_at` | Nullable(DateTime64(3, 'UTC')) |
| `arrived_at` | Nullable(DateTime64(3, 'UTC')) |
| `started_at` | Nullable(DateTime64(3, 'UTC')) |
| `ended_at` | Nullable(DateTime64(3, 'UTC')) |
| `match_s` | Nullable(Int32) |
| `accept_s` | Nullable(Int32) |
| `arrive_s` | Nullable(Int32) |
| `wait_s` | Nullable(Int32) |
| `ride_s` | Nullable(Int32) |
| `total_s` | Nullable(Int32) |
| `cancelled_after_arrival` | UInt8 |
| `route_km` | Nullable(Float64) |
| `predicted_duration_s` | Nullable(UInt32) |
| `actual_duration_s` | Nullable(UInt32) |
| `surge_multiplier` | Nullable(Float64) |
| `fare_final` | Nullable(Decimal64(2)) |
| `driver_payout` | Nullable(Decimal64(2)) |
| `payment_method` | Nullable(Enum8('card' = 1, 'wallet' = 2, 'cash' = 3)) |
| `cancellation_reason` | Nullable(Enum8( 'rider_no_show' = 1, 'driver_too_far' = 2, 'vehicle_issue' = 3, 'changed_mind' = 4, 'found_alternative' = 5, 'wait_too_long' = 6 )) |
| `event_time` | DateTime64(3, 'UTC') |
| `event_date` | Date MATERIALIZED toDate(event_time) |

### `hailpath.fulfilment_hourly`

| Column | Type |
| --- | --- |
| `hour` | DateTime('UTC') |
| `pickup_zone_id` | UInt16 |
| `trips_ended` | UInt32 |
| `completed` | UInt32 |
| `cancelled_by_passenger` | UInt32 |
| `cancelled_by_driver` | UInt32 |
| `no_driver_found` | UInt32 |
| `cancelled_after_arrival` | UInt32 |
| `match_s_sum` | UInt64 |
| `matched_trips` | UInt32 |
| `wait_s_sum` | UInt64 |
| `waited_trips` | UInt32 |

### `hailpath.driver_activity_hourly`

| Column | Type |
| --- | --- |
| `hour` | DateTime('UTC') |
| `zone_id` | UInt16 |
| `positions` | SimpleAggregateFunction(sum, UInt64) |
| `drivers` | AggregateFunction(uniq, UInt32) |
| `idle_positions` | SimpleAggregateFunction(sum, UInt64) |
| `en_route_positions` | SimpleAggregateFunction(sum, UInt64) |
| `on_trip_positions` | SimpleAggregateFunction(sum, UInt64) |
| `speed_kmh_sum` | SimpleAggregateFunction(sum, Float64) |
| `speed_samples` | SimpleAggregateFunction(sum, UInt64) |
| `max_speed_kmh` | SimpleAggregateFunction(max, Float32) |

### `hailpath.rider_activity_hourly`

| Column | Type |
| --- | --- |
| `hour` | DateTime('UTC') |
| `zone_id` | UInt16 |
| `positions` | SimpleAggregateFunction(sum, UInt64) |
| `riders` | AggregateFunction(uniq, UInt32) |
| `trips` | AggregateFunction(uniq, UInt64) |
| `accuracy_m_sum` | SimpleAggregateFunction(sum, Float64) |
| `accuracy_samples` | SimpleAggregateFunction(sum, UInt64) |
