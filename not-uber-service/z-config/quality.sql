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
-- Q0 first, because every bar after it counts VIOLATIONS - and a count of
-- violations is zero on an empty warehouse, which is a worse answer than
-- no answer: it says the data is sound when there is no data. Checked as
-- "are there rows" rather than "are there recent rows",
-- because straight after bootstrap the only rows are seeded history whose
-- timestamps are deliberately in the past.
nothing_to_judge AS (
    -- Every comparison is parenthesised. Without the parens on the first
    -- one, `+` binds tighter than `=` and the whole thing reads as
    -- count(trip_events) = (0 + 1 + 1 + 1), which answered 0 on an empty
    -- warehouse and reported ok - the exact false pass this bar exists to
    -- prevent, in the bar itself.
    SELECT ((SELECT count() FROM nus.trip_events) = 0)
         + ((SELECT count() FROM nus.dispatch_offers) = 0)
         + ((SELECT count() FROM nus.driver_positions) = 0)
         + ((SELECT count() FROM nus.trip_facts) = 0) AS n
),
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
-- Every terminal trip must have produced a trip_facts row. A gap means the
-- materialized view is not keeping up, or is not firing.
--
-- An anti-join, not a subtraction of two counts, and the difference is not
-- cosmetic. The old form read trip_events and trip_facts as two separate
-- scans and subtracted them, so on a live stream it reported whatever
-- arrived between the two reads: it said 3 on a healthy stack. It could
-- also have read zero while trips were BOTH missing and duplicated, since
-- the two errors cancel in a difference.
--
-- The settle window is the same five minutes Q14 uses, and for the same
-- reason: the fact is written in the SAME insert as the event, so any
-- trip older than the window has had its fact for just as long, whichever
-- order the two scans happen to run in.
--
-- Nothing is lost by dropping the negative case. trip_facts is read FINAL
-- and ordered by trip_id, so FINAL collapses it to one row per trip -
-- "more facts than trips" was never reachable except through the race
-- this replaces.
facts_missing AS (
    SELECT uniqExact(trip_id) AS n
    FROM nus.trip_events
    WHERE status IN ('completed', 'cancelled_by_passenger',
                     'cancelled_by_driver', 'no_driver_found')
      AND event_time < now() - INTERVAL 5 MINUTE
      AND trip_id NOT IN (SELECT trip_id FROM nus.trip_facts)
),
-- An offer for a trip the warehouse has never heard of - but only once
-- the trip has had time to appear.
--
-- Without the window this is not a structural bar at all, it is a
-- stopwatch. Dispatch writes the offer FIRST and the trip's first
-- lifecycle row second: trip_events has no 'requested' status, so a
-- trip's earliest row is 'matched' or 'no_driver_found', and both are
-- written only after the offer chain has resolved. Every trip currently
-- being dispatched therefore has offers and no trip row, correctly.
--
-- It read 1 on a live stack and called it a defect. Five minutes clears
-- the whole path with room: an offer chain is at most five offers with a
-- twenty-second deadline each, and the sink batches every five seconds.
-- Past that, an offer with no trip is a real orphan.
orphan_offers AS (
    SELECT uniqExact(trip_id) AS n FROM nus.dispatch_offers
    WHERE offered_at < now() - INTERVAL 5 MINUTE
      AND trip_id NOT IN (SELECT trip_id FROM nus.trip_events)
)
-- measured is Int64 throughout: countIf answers UInt64 while the two
-- subtractions answer Int64, and a UNION has to settle on one. Signed
-- is the right choice, not a cast of convenience - a negative here
-- means MORE facts than terminal trips, which is its own bug and must
-- not wrap into a huge positive.
SELECT bar, measured, pass_line, if(measured = 0, 'ok', 'FAIL') AS verdict
FROM (
    SELECT 'Q0  there is data to judge'          AS bar, toInt64((SELECT n FROM nothing_to_judge))      AS measured, 'empty tables = 0' AS pass_line
    UNION ALL SELECT 'Q1  every event id is unique',      toInt64((SELECT n FROM dupes)),                 'duplicates = 0'
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
-- Q8 joins dispatch_offers to trip_facts and Q14 checks dispatch_offers
-- against trip_events. Both tables are on the one ClickHouse.
