-- What the warehouse costs per row, and what that becomes at full scale.
--
-- The projection is the deliverable, not the current size: dev scale is
-- deliberately thin, so today's total says nothing. Bytes per row does
-- transfer, because a row's width is decided by the schema rather than by
-- how many rows there are - provided enough rows have been written for
-- parts to have merged, which is why this is read after real traffic and
-- not after a bare bring-up.
--
-- {scale:Float64} is how many times bigger full scale is than this run.
-- The Makefile computes it from .env: full-scale SEED_DRIVERS divided by
-- the value actually in use. Passed in rather than hardcoded, so a
-- different dev profile does not silently invalidate the answer.

SELECT '=== per-table cost, measured ===' AS section FORMAT TSVRaw;
SELECT
    table,
    -- Never aliased 'rows': that shadows system.parts.rows and the
    -- WHERE below then reads the aggregate, which ClickHouse refuses.
    sum(rows)                                            AS row_count,
    formatReadableSize(sum(bytes_on_disk))               AS on_disk,
    formatReadableSize(sum(data_uncompressed_bytes))     AS uncompressed,
    round(sum(data_uncompressed_bytes) / sum(bytes_on_disk), 1) AS compression_x,
    -- The number that transfers to any scale.
    round(sum(bytes_on_disk) / sum(rows), 1)             AS bytes_per_row,
    count()                                              AS parts,
    uniqExact(partition)                                 AS partitions
FROM system.parts
WHERE database = 'nus' AND active AND rows > 0
GROUP BY table
ORDER BY sum(bytes_on_disk) DESC;

SELECT '=== projection to full scale ===' AS section FORMAT TSVRaw;
-- Two multipliers, not one. An earlier version of this projected
-- on_disk_now x fleet_scale and answered 65 MiB per node for
-- driver_positions, which was nonsense: the measurement covered about
-- forty minutes of traffic while the table's TTL keeps THREE DAYS. Scaling
-- the fleet without scaling the window understates the heaviest table by
-- roughly a hundredfold, and understating it is the one direction that
-- matters - the projection exists to say whether the disk survives.
--
-- So: measure the live ingest RATE, scale that by the fleet, and hold it
-- for as long as the TTL says. The TTL is read out of the table's own DDL
-- rather than repeated here, so the two cannot drift.
--
-- The rate is measured over the last five minutes of real traffic, which
-- means this must be read from a warm stack. On a stack that has only just
-- seeded, the rate is zero and the projection says zero - correctly, and
-- uselessly.
--
-- One assumption, stated rather than buried: scaling every rate by the
-- fleet ratio takes supply and demand to be scaled together. They are in
-- the full-scale profile, and step 12 requires every intermediate rung to
-- hold that ratio too. A dev profile that starves supply will project a
-- trip-side volume that is too low.
WITH 300 AS window_s
SELECT
    t.table,
    t.rows_per_s                                                  AS rows_per_s_now,
    round(t.rows_per_s * {scale:Float64}, 1)                      AS rows_per_s_full,
    d.ttl_days,
    p.bytes_per_row,
    formatReadableSize(p.bytes_per_row * t.rows_per_s * {scale:Float64}
                       * d.ttl_days * 86400)                      AS cluster_at_ttl,
    -- Per NODE, which is what the quota caps: two shards split the rows,
    -- and each shard's replica holds a full copy of its own shard.
    formatReadableSize(p.bytes_per_row * t.rows_per_s * {scale:Float64}
                       * d.ttl_days * 86400 / 2)                  AS per_node_at_ttl
FROM (
    SELECT 'driver_positions_local' AS table,
           count() / window_s AS rows_per_s FROM nus.driver_positions
     WHERE event_time > now() - toIntervalSecond(window_s)
    UNION ALL SELECT 'rider_positions_local', count() / window_s FROM nus.rider_positions
     WHERE event_time > now() - toIntervalSecond(window_s)
    UNION ALL SELECT 'trip_events_local', count() / window_s FROM nus.trip_events
     WHERE event_time > now() - toIntervalSecond(window_s)
    UNION ALL SELECT 'dispatch_offers_local', count() / window_s FROM nus.dispatch_offers
     WHERE event_time > now() - toIntervalSecond(window_s)
    UNION ALL SELECT 'hotspot_history_local', count() / window_s FROM nus.hotspot_history
     WHERE computed_at > now() - toIntervalSecond(window_s)
) AS t
INNER JOIN (
    SELECT table, sum(bytes_on_disk) / sum(rows) AS bytes_per_row
    FROM system.parts WHERE database = 'nus' AND active AND rows > 0
    GROUP BY table
) AS p ON p.table = t.table
INNER JOIN (
    -- The retention each table actually declares, taken from its own DDL.
    -- Matched as toIntervalDay(N), not `INTERVAL N DAY`: ClickHouse
    -- normalises the TTL expression before storing it, so the wording in
    -- 002_positions.sql is not the wording system.tables returns.
    SELECT name AS table,
           toUInt32OrZero(extract(create_table_query, 'toIntervalDay\\((\\d+)\\)')) AS ttl_days
    FROM system.tables WHERE database = 'nus'
) AS d ON d.table = t.table
WHERE d.ttl_days > 0
ORDER BY p.bytes_per_row * t.rows_per_s * d.ttl_days DESC
SETTINGS distributed_product_mode = 'local';

SELECT '=== does it fit the quota ===' AS section FORMAT TSVRaw;
-- Headroom is what is left after the projection. A warehouse planned to
-- exactly fill its disk cannot merge: a merge needs room for the new part
-- before it can drop the parts it replaces.
WITH 300 AS window_s
SELECT
    formatReadableSize(sum(per_node))                             AS per_node_projected,
    formatReadableSize({quota_gb:Float64} * 1024 * 1024 * 1024)   AS quota,
    round(100 * sum(per_node) / ({quota_gb:Float64} * 1024 * 1024 * 1024), 1) AS pct_of_quota,
    if(sum(per_node) < {quota_gb:Float64} * 1024 * 1024 * 1024 * 0.7,
       'ok', 'FAIL')                                              AS verdict,
    '30% headroom' AS pass_line
FROM (
    SELECT p.bytes_per_row * t.rows_per_s * {scale:Float64} * d.ttl_days * 86400 / 2 AS per_node
    FROM (
        SELECT 'driver_positions_local' AS table,
               count() / window_s AS rows_per_s FROM nus.driver_positions
         WHERE event_time > now() - toIntervalSecond(window_s)
        UNION ALL SELECT 'rider_positions_local', count() / window_s FROM nus.rider_positions
         WHERE event_time > now() - toIntervalSecond(window_s)
        UNION ALL SELECT 'trip_events_local', count() / window_s FROM nus.trip_events
         WHERE event_time > now() - toIntervalSecond(window_s)
        UNION ALL SELECT 'dispatch_offers_local', count() / window_s FROM nus.dispatch_offers
         WHERE event_time > now() - toIntervalSecond(window_s)
        UNION ALL SELECT 'hotspot_history_local', count() / window_s FROM nus.hotspot_history
         WHERE computed_at > now() - toIntervalSecond(window_s)
    ) AS t
    INNER JOIN (
        SELECT table, sum(bytes_on_disk) / sum(rows) AS bytes_per_row
        FROM system.parts WHERE database = 'nus' AND active AND rows > 0
        GROUP BY table
    ) AS p ON p.table = t.table
    INNER JOIN (
        SELECT name AS table,
               toUInt32OrZero(extract(create_table_query, 'toIntervalDay\\((\\d+)\\)')) AS ttl_days
        FROM system.tables WHERE database = 'nus'
    ) AS d ON d.table = t.table
    WHERE d.ttl_days > 0
)
SETTINGS distributed_product_mode = 'local';

SELECT '=== TTL: is it dropping partitions or deleting rows ===' AS section FORMAT TSVRaw;
-- A TTL that has to delete inside a monthly part rewrites the whole part.
-- Dropping a partition is a directory removal. The difference is invisible
-- at dev scale and decides whether the heaviest table survives at full
-- scale, so the partition span is checked rather than assumed.
SELECT
    table,
    uniqExact(partition)                                  AS partitions,
    min(partition)                                        AS oldest,
    max(partition)                                        AS newest,
    round(sum(rows) / uniqExact(partition))               AS rows_per_partition
FROM system.parts
WHERE database = 'nus' AND active AND rows > 0
  AND table IN ('driver_positions_local', 'rider_positions_local', 'trip_events_local')
GROUP BY table ORDER BY table;
