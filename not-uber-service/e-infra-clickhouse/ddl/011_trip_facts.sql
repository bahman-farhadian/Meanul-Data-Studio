-- One row per trip, and the funnel that finally counts the trips that did
-- not complete.
--
-- trip_events is transaction grain: six or more rows per trip, kept a year.
-- Every rollup built on it so far filters status = 'completed', which means
-- cancellations and unmatched requests appear in NO aggregate this
-- warehouse produces - fulfilment rate, the most basic health metric a
-- ride-hail platform has, was a self-join over a year of raw events.
--
-- Kimball's name for one row per item moving through pipeline milestones is
-- an accumulating snapshot, and a trip is the textbook case for it. The
-- milestone times are carried on the terminal message from the trips row
-- (see trip_lifecycle.avsc), not reconstructed here from event arrival
-- order - so these are the times the OLTP store recorded, and the lags
-- below are computed once on write instead of in every query.

CREATE TABLE IF NOT EXISTS nus.trip_facts_local ON CLUSTER nus_cluster
(
    event_id                UUID,
    trip_id                 FixedString(21),
    rider_id                FixedString(11),
    driver_id               Nullable(FixedString(11)),
    -- How the trip ended. Only terminal statuses reach this table, so this
    -- is never a status the trip merely passed through.
    final_status            Enum8(
                                'completed' = 6, 'cancelled_by_passenger' = 7,
                                'cancelled_by_driver' = 8, 'no_driver_found' = 9
                            ),
    pickup_zone_id          LowCardinality(String),
    dropoff_zone_id         LowCardinality(String),
    passenger_count         UInt8,
    requested_vehicle_type  Enum8('economy' = 1, 'xl' = 2, 'premium' = 3),

    requested_at            Nullable(DateTime64(3, 'UTC')),
    matched_at              Nullable(DateTime64(3, 'UTC')),
    accepted_at             Nullable(DateTime64(3, 'UTC')),
    arrived_at              Nullable(DateTime64(3, 'UTC')),
    started_at              Nullable(DateTime64(3, 'UTC')),
    ended_at                Nullable(DateTime64(3, 'UTC')),

    -- The lags every funnel question actually asks for, computed here once.
    -- Int32 rather than UInt32 on purpose: a negative value is impossible
    -- if dispatch is correct, and an unsigned column would wrap it into a
    -- huge positive number instead of showing the bug.
    match_s                 Nullable(Int32),
    accept_s                Nullable(Int32),
    arrive_s                Nullable(Int32),
    -- How long the rider kept the driver waiting at the kerb. The one
    -- measure the whole 'arrived' state exists for.
    wait_s                  Nullable(Int32),
    ride_s                  Nullable(Int32),
    total_s                 Nullable(Int32),
    -- True when the trip was cancelled after the driver had already
    -- arrived - the line a real platform draws to charge a cancellation
    -- fee, and something that could not be asked before arrived_at existed.
    cancelled_after_arrival UInt8,

    route_km                Nullable(Float64),
    predicted_duration_s    Nullable(UInt32),
    actual_duration_s       Nullable(UInt32),
    surge_multiplier        Nullable(Float64),
    fare_final              Nullable(Decimal64(2)),
    driver_payout           Nullable(Decimal64(2)),
    payment_method          Nullable(Enum8('card' = 1, 'wallet' = 2, 'cash' = 3)),
    cancellation_reason     Nullable(Enum8(
                                'rider_no_show' = 1, 'driver_too_far' = 2,
                                'vehicle_issue' = 3, 'changed_mind' = 4,
                                'found_alternative' = 5, 'wait_too_long' = 6
                            )),

    event_time              DateTime64(3, 'UTC'),
    event_date              Date MATERIALIZED toDate(event_time)
)
-- ReplacingMergeTree keyed on the trip, with event_time as the version.
-- A trip reaches exactly one terminal status, so there is exactly one row
-- per trip in normal operation and the engine has nothing to collapse. It
-- is here as the safety net for a replay the sink's event_id check somehow
-- missed - and because a duplicate that survives both would otherwise
-- double a trip in every chart. Read with FINAL when an exact count
-- matters; the row count without it is an upper bound.
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}', event_time)
-- Daily, because the TTL is now shorter than a month. Under monthly
-- partitions a 30-day expiry drops nothing until an entire month has
-- aged out, so the table would hold up to sixty days to honour a
-- thirty-day contract. Daily means expiry removes exactly one day's
-- directory, and thirty-odd partitions is a healthy count.
PARTITION BY event_date
ORDER BY trip_id
-- Thirty days, cut from ninety and originally a year. Measured each time
-- with make capacity rather than chosen: this project's full-scale scope is
-- SEVEN DAYS of history, so thirty days is still over four times the data
-- that will ever exist in it.
TTL event_date + INTERVAL 30 DAY;

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.trip_facts_mv ON CLUSTER nus_cluster
TO nus.trip_facts_local
AS
SELECT
    event_id,
    trip_id,
    rider_id,
    driver_id,
    status                                          AS final_status,
    pickup_zone_id,
    dropoff_zone_id,
    passenger_count,
    requested_vehicle_type,
    requested_at,
    matched_at,
    accepted_at,
    arrived_at,
    started_at,
    ended_at,
    dateDiff('second', requested_at, matched_at)    AS match_s,
    dateDiff('second', matched_at,   accepted_at)   AS accept_s,
    dateDiff('second', accepted_at,  arrived_at)    AS arrive_s,
    dateDiff('second', arrived_at,   started_at)    AS wait_s,
    dateDiff('second', started_at,   ended_at)      AS ride_s,
    dateDiff('second', requested_at, ended_at)      AS total_s,
    if(status IN ('cancelled_by_passenger', 'cancelled_by_driver')
       AND arrived_at IS NOT NULL, 1, 0)            AS cancelled_after_arrival,
    route_km,
    predicted_duration_s,
    actual_duration_s,
    surge_multiplier,
    fare_final,
    driver_payout,
    payment_method,
    cancellation_reason,
    event_time
FROM nus.trip_events_local
WHERE status IN ('completed', 'cancelled_by_passenger', 'cancelled_by_driver', 'no_driver_found');

CREATE TABLE IF NOT EXISTS nus.trip_facts ON CLUSTER nus_cluster
AS nus.trip_facts_local
ENGINE = Distributed(nus_cluster, nus, trip_facts_local, cityHash64(trip_id));

-- The funnel, per zone per hour. Sourced from trip_facts_local, not
-- trip_events_local: one row per trip is already the right grain for
-- "how many requests ended how", and counting terminal events straight
-- from the event stream would be the same numbers for more work.
CREATE TABLE IF NOT EXISTS nus.fulfilment_hourly_local ON CLUSTER nus_cluster
(
    hour                    DateTime('UTC'),
    pickup_zone_id          LowCardinality(String),
    trips_ended             UInt64,
    completed               UInt64,
    cancelled_by_passenger  UInt64,
    cancelled_by_driver     UInt64,
    no_driver_found         UInt64,
    cancelled_after_arrival UInt64,
    -- Summed, divided at query time - never a stored ratio.
    match_s_sum             UInt64,
    matched_trips           UInt64,
    wait_s_sum              UInt64,
    waited_trips            UInt64
)
ENGINE = ReplicatedSummingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, pickup_zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.fulfilment_hourly_mv ON CLUSTER nus_cluster
TO nus.fulfilment_hourly_local
AS
SELECT
    -- Bucketed on when the ride was ASKED for, not when it ended: a
    -- fulfilment rate is about the demand that arrived in an hour, and
    -- bucketing on the end time would credit a long trip to the wrong one.
    toStartOfHour(ifNull(requested_at, event_time))      AS hour,
    pickup_zone_id,
    count()                                              AS trips_ended,
    countIf(final_status = 'completed')                  AS completed,
    countIf(final_status = 'cancelled_by_passenger')     AS cancelled_by_passenger,
    countIf(final_status = 'cancelled_by_driver')        AS cancelled_by_driver,
    countIf(final_status = 'no_driver_found')            AS no_driver_found,
    sum(cancelled_after_arrival)                         AS cancelled_after_arrival,
    sum(ifNull(toUInt64(greatest(match_s, 0)), 0))       AS match_s_sum,
    countIf(match_s IS NOT NULL)                         AS matched_trips,
    sum(ifNull(toUInt64(greatest(wait_s, 0)), 0))        AS wait_s_sum,
    countIf(wait_s IS NOT NULL)                          AS waited_trips
FROM nus.trip_facts_local
GROUP BY hour, pickup_zone_id;

CREATE TABLE IF NOT EXISTS nus.fulfilment_hourly ON CLUSTER nus_cluster
AS nus.fulfilment_hourly_local
ENGINE = Distributed(nus_cluster, nus, fulfilment_hourly_local, cityHash64(pickup_zone_id));

-- Fulfilment rate:   sum(completed) / sum(trips_ended)
-- Cancellation rate: (sum(cancelled_by_passenger) + sum(cancelled_by_driver))
--                    / sum(trips_ended)
-- Mean time-to-match: sum(match_s_sum) / sum(matched_trips)
-- Mean rider wait at the kerb: sum(wait_s_sum) / sum(waited_trips)
