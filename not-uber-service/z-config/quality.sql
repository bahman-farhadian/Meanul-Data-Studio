-- Data-quality bars: does the warehouse hold data that can be trusted.
--
-- Every bar below is STRUCTURAL - an invariant that must hold whatever the
-- seed settings are. That is deliberate and it is the difference between a
-- bar and a thermometer. "Fulfilment above 0.8" would pass at full scale
-- and fail at dev scale, so it would be measuring SEED_DRIVERS against
-- TRIP_REQUESTS_PER_MINUTE rather than measuring the pipeline. Anything
-- whose value moves with scale belongs in profile.sql, which is for
-- reading; this file is for passing or failing.
--
-- Every row answers with a verdict, not a number to interpret. measured
-- and pass_line are there so a failure says how far off it is.
-- make verify-quality exits non-zero when any row says FAIL.

SELECT '=== data-quality bars ===' AS section FORMAT TSVRaw;

WITH
-- Uniqueness, first, because every other count here depends on it.
-- ClickHouse does not deduplicate: ReplacingMergeTree collapses only
-- eventually, only within a partition, and only on merge. If this fails,
-- nothing below it means anything.
dupes AS (
    SELECT
        (SELECT count() - uniqExact(event_id) FROM nus.trip_events)
      + (SELECT count() - uniqExact(event_id) FROM nus.dispatch_offers)
      + (SELECT count() - uniqExact(event_id) FROM nus.driver_positions)
      + (SELECT count() - uniqExact(event_id) FROM nus.rider_positions)
      + (SELECT count() - uniqExact(event_id) FROM nus.hotspot_history) AS n
),
blank_ids AS (
    SELECT
        (SELECT countIf(event_id = toUUID('00000000-0000-0000-0000-000000000000')) FROM nus.trip_events)
      + (SELECT countIf(event_id = toUUID('00000000-0000-0000-0000-000000000000')) FROM nus.dispatch_offers)
      + (SELECT countIf(event_id = toUUID('00000000-0000-0000-0000-000000000000')) FROM nus.driver_positions) AS n
),
-- A trip's clock must run forwards. Int32 on purpose, so a state-machine
-- bug shows as a negative number instead of wrapping into a huge positive.
bad_lags AS (
    SELECT countIf(match_s < 0 OR accept_s < 0 OR arrive_s < 0
                   OR wait_s < 0 OR ride_s < 0 OR total_s < 0) AS n
    FROM nus.trip_facts FINAL
),
-- Arrival cannot precede acceptance, and a trip cannot start before the
-- car got there.
bad_arrival AS (
    SELECT countIf(arrived_at IS NOT NULL AND accepted_at IS NULL)
         + countIf(started_at IS NOT NULL AND arrived_at IS NULL
                   AND final_status = 'completed') AS n
    FROM nus.trip_facts FINAL
),
-- A no-show can only be declared by a driver who was actually waiting,
-- and "waited too long" needs something to have waited for.
impossible_reason AS (
    SELECT countIf(cancellation_reason IN ('rider_no_show', 'wait_too_long')
                   AND arrived_at IS NULL) AS n
    FROM nus.trip_facts FINAL
),
-- One trip, one driver. Two acceptances means two cars sent to one rider.
two_accepted AS (
    SELECT countIf(accepted > 1) AS n
    FROM (SELECT trip_id, countIf(status = 'accepted') AS accepted
          FROM nus.dispatch_offers GROUP BY trip_id)
),
-- An offer chain is 1, 2, 3 ... with nothing missing. A gap means dispatch
-- lost track of its own search.
chain_gaps AS (
    SELECT countIf(offers != top_sequence OR top_sequence = 0) AS n
    FROM (SELECT trip_id, count() AS offers, max(sequence) AS top_sequence
          FROM nus.dispatch_offers GROUP BY trip_id)
),
-- A trip nobody took cannot have an acceptance on record.
accepted_but_unmatched AS (
    SELECT count() AS n FROM (
        SELECT trip_id FROM nus.trip_facts FINAL WHERE final_status = 'no_driver_found'
    ) AS t
    INNER JOIN (
        SELECT trip_id FROM nus.dispatch_offers WHERE status = 'accepted'
    ) AS o USING trip_id
),
-- A party of five cannot travel in a four-seat car. economy and premium
-- seat four; only xl seats six.
party_over_seats AS (
    SELECT countIf(passenger_count > 4 AND requested_vehicle_type != 'xl') AS n
    FROM nus.trip_events
),
-- Money exists exactly when the trip was completed, and never otherwise.
-- A fare on a cancelled trip is revenue that was never earned.
fare_without_completion AS (
    SELECT countIf(final_status != 'completed' AND fare_final IS NOT NULL) AS n
    FROM nus.trip_facts FINAL
),
completion_without_fare AS (
    SELECT countIf(final_status = 'completed' AND fare_final IS NULL) AS n
    FROM nus.trip_facts FINAL
),
-- The platform takes a cut; it does not pay the driver more than the rider
-- paid. This is the arithmetic the whole Decimal change exists to protect.
payout_over_fare AS (
    SELECT countIf(driver_payout > fare_final) AS n
    FROM nus.trip_facts FINAL WHERE fare_final IS NOT NULL
),
-- Every terminal trip must have produced exactly one trip_facts row. A gap
-- means the materialized view is not keeping up, or is not firing.
facts_missing AS (
    SELECT (SELECT uniqExact(trip_id) FROM nus.trip_events
             WHERE status IN ('completed', 'cancelled_by_passenger',
                              'cancelled_by_driver', 'no_driver_found'))
         - (SELECT count() FROM nus.trip_facts FINAL) AS n
),
-- An offer for a trip the warehouse has never heard of.
orphan_offers AS (
    SELECT uniqExact(trip_id) AS n FROM nus.dispatch_offers
    WHERE trip_id NOT IN (SELECT trip_id FROM nus.trip_events)
)
-- measured is Int64 throughout: countIf answers UInt64 while the two
-- subtractions answer Int64, and a UNION has to settle on one. Signed
-- is the right choice, not a cast of convenience - a negative here
-- means MORE facts than terminal trips, which is its own bug and must
-- not wrap into a huge positive.
SELECT bar, measured, pass_line, if(measured = 0, 'ok', 'FAIL') AS verdict
FROM (
    SELECT 'Q1  every event id is unique'        AS bar, toInt64((SELECT n FROM dupes))                  AS measured, 'duplicates = 0' AS pass_line
    UNION ALL SELECT 'Q2  no blank event id',             toInt64((SELECT n FROM blank_ids)),             'blank = 0'
    UNION ALL SELECT 'Q3  no negative milestone lag',     toInt64((SELECT n FROM bad_lags)),              'negative = 0'
    UNION ALL SELECT 'Q4  arrival follows acceptance',    toInt64((SELECT n FROM bad_arrival)),           'out of order = 0'
    UNION ALL SELECT 'Q5  no-show only after arrival',    toInt64((SELECT n FROM impossible_reason)),     'impossible = 0'
    UNION ALL SELECT 'Q6  one acceptance per trip',       toInt64((SELECT n FROM two_accepted)),          'double-matched = 0'
    UNION ALL SELECT 'Q7  offer chains have no gaps',     toInt64((SELECT n FROM chain_gaps)),            'gapped chains = 0'
    UNION ALL SELECT 'Q8  unmatched trips took no offer', toInt64((SELECT n FROM accepted_but_unmatched)),'contradictions = 0'
    UNION ALL SELECT 'Q9  party fits the tier seats',     toInt64((SELECT n FROM party_over_seats)),      'oversized = 0'
    UNION ALL SELECT 'Q10 no fare without completion',    toInt64((SELECT n FROM fare_without_completion)),'unearned = 0'
    UNION ALL SELECT 'Q11 no completion without fare',    toInt64((SELECT n FROM completion_without_fare)),'unpriced = 0'
    UNION ALL SELECT 'Q12 payout never exceeds fare',     toInt64((SELECT n FROM payout_over_fare)),      'overpaid = 0'
    UNION ALL SELECT 'Q13 every ended trip has a fact',   toInt64((SELECT n FROM facts_missing)),         'missing = 0'
    UNION ALL SELECT 'Q14 no offer for an unknown trip',  toInt64((SELECT n FROM orphan_offers)),         'orphans = 0'
)
ORDER BY bar;
