-- The one thing ksqlDB does here that nothing else in the stack does.
--
-- Everything in 010 is a view over a topic: useful for a SQL client, but it
-- computes nothing. This file adds a materialized table, and the question is
-- fair - why, when nus.dispatch_funnel_hourly already exists in ClickHouse?
--
-- Because of latency and granularity, which are the two things that rollup
-- cannot give. A record reaches that rollup through the sink's batch (up to
-- five seconds, by design - see n-service-clickhouse-sink/batches.py) and
-- lands in an hourly bucket. "Is this zone starving right now" is a
-- question about the last sixty seconds, and an hourly bucket answers it an
-- hour late. This table answers it from the offer stream itself, per minute,
-- as a point lookup on a key.
--
-- WHY THE STATE IS BOUNDED, DELIBERATELY.
--
-- The obvious materialized table here is "current status of every trip",
-- keyed by trip_id with LATEST_BY_OFFSET. It is also a slow leak: at full
-- scale that is 655,000 new keys a day, none of which is ever tombstoned,
-- held in RocksDB on a server with a 1 GB heap. This one is keyed by zone
-- and windowed, so its state is at most (zones x windows in retention) -
-- 263 x 120, and it cannot grow with traffic at all. A live view that
-- outgrows its server stops being a live view.
--
-- SUMS, NOT RATES - the same rule the warehouse follows. A stored
-- acceptance rate could not be re-aggregated across windows or zones
-- without being wrong. The counts are stored; the division happens in the
-- question.
--
-- REPLICAS = 3 is written out rather than left to a default, the same rule
-- create-topics.sh states for every other topic in this cluster. An earlier
-- draft omitted it on the theory that ksqlDB would fall back to the
-- broker's default.replication.factor; it does not, and the sink topic came
-- out with one replica. Checked both ways against cp-ksqldb-server:8.3.1 -
-- omitted gives whatever ksqlDB decides, and an explicit 3 is carried
-- through to the broker, which refuses it outright when three brokers are
-- not there. A one-replica topic inside a three-replica cluster is a
-- silent single point of failure, so the number is stated.
--
-- COUNT_IF does not exist in cp-ksqldb-server:8.3.1 - confirmed, not
-- assumed. SUM(CASE WHEN ...) is the portable spelling.

CREATE TABLE IF NOT EXISTS offer_funnel_by_zone_1m
WITH (
    KAFKA_TOPIC  = 'ksql_offer_funnel_by_zone_1m',
    VALUE_FORMAT = 'AVRO',
    PARTITIONS   = 3,
    REPLICAS     = 3
) AS
SELECT
    pickup_zone_id,
    CAST(COUNT(*) AS BIGINT)                                          AS offers_made,
    SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END)              AS offers_accepted,
    SUM(CASE WHEN status = 'declined' THEN 1 ELSE 0 END)              AS offers_declined,
    -- Kept apart from declined on purpose, the same way the Avro enum and
    -- the ClickHouse Enum8 keep them apart: a driver who said no is a
    -- different supply signal from a driver whose phone was face-down.
    SUM(CASE WHEN status = 'expired'  THEN 1 ELSE 0 END)              AS offers_expired,
    SUM(CASE WHEN eta_seconds IS NULL THEN 0 ELSE eta_seconds END)    AS eta_seconds_sum,
    SUM(CASE WHEN eta_seconds IS NULL THEN 0 ELSE 1 END)              AS eta_offers
FROM dispatch_offers
-- GRACE PERIOD, not zero: an offer whose event_time is a few seconds behind
-- the stream still belongs to its own minute. RETENTION is what bounds the
-- state, and it must cover the window plus the grace.
WINDOW TUMBLING (SIZE 1 MINUTE, RETENTION 2 HOURS, GRACE PERIOD 1 MINUTE)
WHERE pickup_zone_id IS NOT NULL
GROUP BY pickup_zone_id
EMIT CHANGES;

-- Read it as a point lookup, which is what a materialized table is for:
--
--   SELECT pickup_zone_id, offers_made, offers_accepted,
--          eta_seconds_sum / eta_offers AS mean_eta_s
--   FROM offer_funnel_by_zone_1m
--   WHERE pickup_zone_id = '142';
