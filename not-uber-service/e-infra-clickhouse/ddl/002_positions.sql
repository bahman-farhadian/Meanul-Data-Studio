-- Where drivers and riders were, over time.
--
-- One table per stream. Services and dashboards query these names.

CREATE TABLE IF NOT EXISTS nus.driver_positions
(
    -- Ids are FixedString, not String: every one of these is a fixed-width
    -- format by construction (see z-lib/nus-common/nus_common/ids.py, the
    -- one place that mints them), so a variable-length column with its own
    -- length prefix would only cost more to store and compare for no
    -- benefit. A row that does not fit this width is a bug upstream, not
    -- something the warehouse should quietly accept.
    -- The message's own identity, from the Avro envelope every producer
    -- stamps. UUID is 16 bytes against 36 for the text form, and the sink
    -- refuses an id it has already written: ClickHouse does not deduplicate
    -- on its own - ReplacingMergeTree only collapses eventually, only within
    -- a partition, and only on merge - so uniqueness has to hold before the
    -- warehouse or no count here can be trusted.
    event_id      UUID,
    driver_id     FixedString(11),
    trip_id       Nullable(FixedString(21)),
    -- Enum, not LowCardinality(String): status is a closed set already
    -- enforced by the Avro schema (DriverStatus) and by Postgres's own
    -- CHECK constraint. Enum8 keeps that same guarantee here - a value
    -- outside this list is a write-time error, not a silently accepted
    -- new category threading through the dictionary.
    status        Enum8('offline' = 1, 'idle' = 2, 'en_route_pickup' = 3, 'on_trip' = 4),
    lat           Float64,
    lon           Float64,
    heading_deg   Nullable(Float32),
    speed_kmh     Nullable(Float32),
    -- LowCardinality(String), not FixedString: the 263 NYC TLC taxi zones
    -- are real external data (city_zones, restored from TLC's own dataset -
    -- see h-bootstrap/lion-prepare/taxi-zones.sql), and TLC's own
    -- LocationID is a variable-width 1-3 digit id ("1".."263"), not a
    -- fixed-width format this codebase controls the way driver_id/trip_id
    -- are (see nus_common/ids.py) - String is the honest type for an id
    -- minted by someone else. LowCardinality still buys the same dictionary
    -- compression a config-sized set like this wants.
    zone_id       LowCardinality(String),
    event_time    DateTime64(3, 'UTC'),
    -- Computed on write and used for partitioning, so queries by day never
    -- have to look at months of data.
    event_date    Date MATERIALIZED toDate(event_time)
)
ENGINE = MergeTree
-- Daily, not monthly, and this is the difference between a TTL that
-- removes a directory and one that rewrites gigabytes. The TTL below is
-- three days; under a monthly partition that expiry can never drop a
-- part, so ClickHouse has to rewrite the whole month minus the expired
-- rows, every time, on the heaviest table in the stack. Measured on a
-- real cluster: every position table held exactly one partition.
--
-- The 365-day tables keep monthly partitions on purpose - daily would
-- give them 365 parts apiece for no benefit, since their expiry drops a
-- whole month cleanly anyway. Partition granularity follows the TTL, not
-- a house style.
PARTITION BY event_date
ORDER BY (driver_id, event_time)
-- Position history is huge and loses value quickly, and this number has
-- now been measured rather than estimated. The comment this replaces said
-- three days "fits real disk with room to spare - re-check against actual
-- observed growth". make capacity did that re-check and it did not fit:
-- at 38.4 bytes/row and 645 rows/s scaled to a real fleet, three days is
-- 79 GiB per node on its own - 83% of a 96 GB quota before any other
-- table, and more than the 30% headroom line allows by itself.
--
-- Two days, therefore. The live map reads Redis rather than this table
-- (see f-infra-grafana), so what this retains is driver-side debugging
-- history, and two days of that is still two days. It takes the table to
-- 53 GiB and the whole warehouse to 59 - comfortably inside the quota
-- with room for a merge to run, which a warehouse planned to exactly fill
-- its disk does not have.
TTL event_date + INTERVAL 2 DAY;


CREATE TABLE IF NOT EXISTS nus.rider_positions
(
    -- rider_id is a passenger id (psg-NNNNNN) - see driver_positions above
    -- for why FixedString, not String.
    -- The message's own identity, from the Avro envelope every producer
    -- stamps. UUID is 16 bytes against 36 for the text form, and the sink
    -- refuses an id it has already written: ClickHouse does not deduplicate
    -- on its own - ReplacingMergeTree only collapses eventually, only within
    -- a partition, and only on merge - so uniqueness has to hold before the
    -- warehouse or no count here can be trusted.
    event_id      UUID,
    rider_id      FixedString(11),
    trip_id       Nullable(FixedString(21)),
    lat           Float64,
    lon           Float64,
    accuracy_m    Nullable(Float32),
    -- LowCardinality(String), not FixedString - see driver_positions above.
    zone_id       LowCardinality(String),
    event_time    DateTime64(3, 'UTC'),
    event_date    Date MATERIALIZED toDate(event_time)
)
ENGINE = MergeTree
-- Daily for the same reason as driver_positions above: a seven-day TTL
-- under a monthly partition never drops anything.
PARTITION BY event_date
ORDER BY (rider_id, event_time)
-- Two days, matching driver_positions.
--
-- This said seven, on the reasoning that "real volume here is tiny next
-- to driver_positions regardless of fleet size". Measurement disagreed.
-- Once the driver-tier fix made trips actually complete, the rate went
-- 14.7 -> 18.4 -> 46.7 rows/s across three readings and the projection
-- went from 6 GiB per node to 13 - more than the entire trip history, for
-- a stream that only the live map and the trip inspector read. A rider
-- reports only while travelling, which is exactly why this scales with
-- FULFILMENT rather than with fleet size, and fulfilment is the thing
-- this project keeps improving.
--
-- Cutting a retention deletes what was answered, so this is only half the
-- change: what day three was being kept for now lives in
-- rider_activity_hourly (012_position_rollups.sql), at 6,144 rows a day
-- whatever the scale.
TTL event_date + INTERVAL 2 DAY;

