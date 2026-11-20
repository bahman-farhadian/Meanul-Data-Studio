-- Every status change of every trip, already enriched by clickhouse-sink.
--
-- The sink adds the columns the raw Kafka message does not carry: the demand
-- score of the pickup zone at that moment, whether the trip counts as a
-- hotspot trip, and how far the real duration drifted from the predicted one.
-- Those lookups come from Redis, never from the OLTP database.

CREATE TABLE IF NOT EXISTS hailpath.trip_events
(
    -- Integer ids this codebase mints (hailpath_common/ids.py). Zones are
    -- TLC LocationID, UInt16. ReplacingMergeTree collapses replay on
    -- event_id; uniqueness still has to hold before the warehouse.
    event_id                   UUID,
    trip_id                    UInt64,
    rider_id                   UInt32,
    driver_id                  Nullable(UInt32),
    -- 'arrived' is 10, appended after 'no_driver_found' rather than placed
    -- in its lifecycle position between en_route_pickup and in_progress.
    -- An Enum8's numbers are what is stored; inserting a symbol in the
    -- middle would renumber everything after it and silently reinterpret
    -- every row already written. The Postgres CHECK and the Avro enum
    -- append in the same order for the same reason.
    status                     Enum8(
                                   'requested' = 1, 'matched' = 2, 'accepted' = 3,
                                   'en_route_pickup' = 4, 'in_progress' = 5, 'completed' = 6,
                                   'cancelled_by_passenger' = 7, 'cancelled_by_driver' = 8,
                                   'no_driver_found' = 9, 'arrived' = 10
                               ),
    pickup_zone_id             UInt16,
    dropoff_zone_id            UInt16,

    -- Party size and the tier asked for. Both existed upstream and neither
    -- ever reached the warehouse, so nothing here could group by tier or
    -- tell a preference apart from a capacity constraint.
    passenger_count            UInt8,
    requested_vehicle_type     Enum8('economy' = 1, 'xl' = 2, 'premium' = 3),

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

    -- Money is Decimal64(2), not Float64. Postgres stores these as
    -- numeric(10,2) and the Avro record carries decimal(10,2), so Float64
    -- was the one copy that did not match - and the mismatch is not only
    -- cosmetic. Float addition is not associative: the same 100,000 values
    -- summed forward and backward give 386991.20000000513 and
    -- 386991.2000000049 (measured, not assumed). The rollups below sum
    -- these columns inside background merges, in an order nobody controls,
    -- so the stored total depends on merge history. The drift is around
    -- 1e-9 relative, so it is not usually a wrong cent - what it is, is a
    -- revenue figure that does not equal itself after two merge orders,
    -- never reconciles exactly against Postgres, and renders as
    -- 41.050000000004 on a dashboard. Decimal64(2) is exact and reconciles.
    fare_estimate              Nullable(Decimal64(2)),
    fare_final                 Nullable(Decimal64(2)),
    -- What the driver took home. fare_final minus this is the platform
    -- take rate, which Postgres has known since migration 011 and the
    -- warehouse could not see at all.
    driver_payout              Nullable(Decimal64(2)),
    payment_method             Nullable(Enum8('card' = 1, 'wallet' = 2, 'cash' = 3)),
    -- Why, within cancelled_by_driver / cancelled_by_passenger. Without it
    -- 'rider never showed up' and 'driver found something better' are the
    -- same row here, and they are very different problems.
    cancellation_reason        Nullable(Enum8(
                                   'rider_no_show' = 1, 'driver_too_far' = 2,
                                   'vehicle_issue' = 3, 'changed_mind' = 4,
                                   'found_alternative' = 5, 'wait_too_long' = 6
                               )),
    -- The trip's own milestone clock, carried on the message from the
    -- trips row rather than reconstructed here from when each event
    -- happened to arrive. This is what lets trip_facts (011) keep one
    -- plain row per trip instead of six event rows and a self-join, and
    -- the times are the ones the OLTP store recorded, not the ones the
    -- pipeline observed. Rider wait at pickup is started_at - arrived_at,
    -- and a cancellation counts as post-arrival exactly when arrived_at
    -- is set.
    requested_at               Nullable(DateTime64(3, 'UTC')),
    matched_at                 Nullable(DateTime64(3, 'UTC')),
    accepted_at                Nullable(DateTime64(3, 'UTC')),
    arrived_at                 Nullable(DateTime64(3, 'UTC')),
    started_at                 Nullable(DateTime64(3, 'UTC')),
    ended_at                   Nullable(DateTime64(3, 'UTC')),

    event_time                 DateTime64(3, 'UTC'),
    event_date                 Date MATERIALIZED toDate(event_time)
)
ENGINE = ReplacingMergeTree(event_time)
PARTITION BY event_date
ORDER BY (event_id)
-- Trips are the business record, so they are kept far longer than positions.
-- Thirty days, cut from ninety and originally a year. Measured each time
-- with make capacity rather than chosen: this project's full-scale scope is
-- SEVEN DAYS of history, so thirty days is still over four times the data
-- that will ever exist in it.
TTL event_date + INTERVAL 30 DAY;


-- One row per trip, last status. Grafana live-ops reads this instead of
-- folding hours of trip_events. Sink writes every lifecycle event.
CREATE TABLE IF NOT EXISTS hailpath.trip_last_status
(
    trip_id                UInt64,
    rider_id               UInt32,
    driver_id              Nullable(UInt32),
    status                 Enum8(
                               'requested' = 1, 'matched' = 2, 'accepted' = 3,
                               'en_route_pickup' = 4, 'in_progress' = 5, 'completed' = 6,
                               'cancelled_by_passenger' = 7, 'cancelled_by_driver' = 8,
                               'no_driver_found' = 9, 'arrived' = 10
                           ),
    pickup_zone_id         UInt16,
    dropoff_zone_id        UInt16,
    predicted_duration_s   Nullable(UInt32),
    fare_estimate          Nullable(Decimal64(2)),
    event_time             DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(event_time)
ORDER BY trip_id
TTL toDate(event_time) + INTERVAL 2 DAY;

