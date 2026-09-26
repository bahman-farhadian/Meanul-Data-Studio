-- Every offer dispatch made, and what the driver said.
--
-- Dispatch used to assign a driver directly and the driver always accepted,
-- so matched -> accepted had nothing in between and the matching funnel did
-- not exist. It now offers: one candidate at a time, with a deadline, and on
-- a decline or a timeout the request moves to the next driver. This table is
-- where acceptance rate, offers per match and time-to-match come from.
--
-- One message per offer, written at its OUTCOME, not two (one when offered
-- and one when answered). An offer in flight is live state and lives in
-- Postgres and Redis; the warehouse wants the completed record. The
-- 'offered' symbol is still in the Enum8 below because dispatch_offers in
-- Postgres genuinely passes through it, and the three copies of a closed
-- set are kept identical on principle - it simply never appears here.

CREATE TABLE IF NOT EXISTS nus.dispatch_offers_local ON CLUSTER nus_cluster
(
    -- Same envelope as every other table here: the sink refuses an
    -- event_id it has already written, because ClickHouse does not
    -- deduplicate on its own.
    event_id             UUID,
    trip_id              FixedString(21),
    driver_id            FixedString(11),
    -- Position in the offer chain for this trip, from 1. A chain of five
    -- means dispatch asked five drivers before one took it, which is the
    -- supply signal - not a detail.
    sequence             UInt8,
    status               Enum8(
                             'offered' = 1, 'accepted' = 2, 'declined' = 3,
                             'expired' = 4, 'cancelled' = 5
                         ),
    -- LowCardinality(String) for the same reason as everywhere else: this
    -- is TLC's own variable-width LocationID, not an id this codebase mints.
    pickup_zone_id       LowCardinality(String),
    -- The two inputs to whether a driver accepts, stored as they were
    -- offered and never recomputed. The ride-sourcing literature finds
    -- pickup time depresses acceptance and surge raises it, so these are
    -- what make the accept/decline split worth analysing at all.
    eta_seconds          Nullable(UInt32),
    distance_to_pickup_m Nullable(UInt32),
    surge_multiplier     Nullable(Float64),
    -- How long the driver took to answer. Null for an offer that expired
    -- unanswered, which is a different outcome from a decline.
    response_s           Nullable(UInt32),
    offered_at           DateTime64(3, 'UTC'),
    event_time           DateTime64(3, 'UTC'),
    event_date           Date MATERIALIZED toDate(offered_at)
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
-- Daily, because the TTL is now shorter than a month. Under monthly
-- partitions a 30-day expiry drops nothing until an entire month has
-- aged out, so the table would hold up to sixty days to honour a
-- thirty-day contract. Daily means expiry removes exactly one day's
-- directory, and thirty-odd partitions is a healthy count.
PARTITION BY event_date
ORDER BY (trip_id, sequence)
-- Kept as long as the trip record it explains. An offer chain is only
-- interesting next to the trip it did or did not produce.
-- Thirty days, cut from ninety and originally a year. Measured each time
-- with make capacity rather than chosen: this project's full-scale scope is
-- SEVEN DAYS of history, so thirty days is still over four times the data
-- that will ever exist in it.
TTL event_date + INTERVAL 30 DAY;

CREATE TABLE IF NOT EXISTS nus.dispatch_offers ON CLUSTER nus_cluster
AS nus.dispatch_offers_local
-- Split by trip, so a whole offer chain lands on one shard - the same rule
-- trip_events follows, and what makes "how deep did this chain go" a
-- single-shard question.
ENGINE = Distributed(nus_cluster, nus, dispatch_offers_local, cityHash64(trip_id));

-- The funnel itself, per zone per hour. Every column is a plain count or
-- sum, so SummingMergeTree merges it with no combinator functions - the
-- same rule 005_rollups.sql sets out.
CREATE TABLE IF NOT EXISTS nus.dispatch_funnel_hourly_local ON CLUSTER nus_cluster
(
    hour              DateTime('UTC'),
    pickup_zone_id    LowCardinality(String),
    offers_made       UInt64,
    offers_accepted   UInt64,
    offers_declined   UInt64,
    offers_expired    UInt64,
    offers_cancelled  UInt64,
    -- Summed, never averaged: divide by the matching count at query time.
    -- An average of per-node averages would be wrong, the same trap
    -- surge_sum avoids in trip_stats_hourly.
    eta_seconds_sum   UInt64,
    eta_offers        UInt64,
    accepted_eta_sum  UInt64,
    response_s_sum    UInt64,
    responses         UInt64
)
ENGINE = ReplicatedSummingMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMM(hour)
ORDER BY (hour, pickup_zone_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS nus.dispatch_funnel_hourly_mv ON CLUSTER nus_cluster
TO nus.dispatch_funnel_hourly_local
AS
SELECT
    toStartOfHour(offered_at)                          AS hour,
    pickup_zone_id,
    count()                                            AS offers_made,
    countIf(status = 'accepted')                       AS offers_accepted,
    countIf(status = 'declined')                       AS offers_declined,
    countIf(status = 'expired')                        AS offers_expired,
    countIf(status = 'cancelled')                      AS offers_cancelled,
    sum(ifNull(eta_seconds, 0))                        AS eta_seconds_sum,
    countIf(eta_seconds IS NOT NULL)                   AS eta_offers,
    sumIf(ifNull(eta_seconds, 0), status = 'accepted') AS accepted_eta_sum,
    sum(ifNull(response_s, 0))                         AS response_s_sum,
    countIf(response_s IS NOT NULL)                    AS responses
FROM nus.dispatch_offers_local
GROUP BY hour, pickup_zone_id;

CREATE TABLE IF NOT EXISTS nus.dispatch_funnel_hourly ON CLUSTER nus_cluster
AS nus.dispatch_funnel_hourly_local
ENGINE = Distributed(nus_cluster, nus, dispatch_funnel_hourly_local, cityHash64(pickup_zone_id));

-- Acceptance rate:  SELECT hour, sum(offers_accepted) / sum(offers_made)
--   FROM dispatch_funnel_hourly GROUP BY hour
-- Offers per match: sum(offers_made) / sum(offers_accepted)
-- Mean ETA offered: sum(eta_seconds_sum) / sum(eta_offers)
-- Whether a shorter drive really is accepted more often:
--   sum(accepted_eta_sum) / sum(offers_accepted) against the line above.
-- Always divide at query time; never read a ratio column, there isn't one.
