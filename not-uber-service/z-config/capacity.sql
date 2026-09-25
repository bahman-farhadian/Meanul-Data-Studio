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
-- Only the tables whose volume scales with the fleet or with demand are
-- projected. A rollup keyed on (hour, zone) does not: its row count is
-- bounded by the calendar and by 263 zones however big the fleet gets,
-- which is the whole reason rollups exist.
SELECT
    table,
    sum(rows)                                             AS row_count,
    round(sum(bytes_on_disk) / sum(rows), 1)              AS bytes_per_row,
    formatReadableSize(sum(bytes_on_disk))                AS on_disk_now,
    formatReadableSize(sum(bytes_on_disk) * {scale:Float64}) AS on_disk_projected,
    -- Per NODE, which is what the quota caps. Two shards, so a
    -- Distributed write lands roughly half its rows on each - and each
    -- shard keeps a replica, so the per-node figure is the shard's own
    -- share, not the cluster total divided by four.
    formatReadableSize(sum(bytes_on_disk) * {scale:Float64} / 2) AS per_node_projected
FROM system.parts
WHERE database = 'nus' AND active AND rows > 0
  AND table IN ('driver_positions_local', 'rider_positions_local',
                'trip_events_local', 'dispatch_offers_local', 'trip_facts_local')
GROUP BY table
ORDER BY sum(bytes_on_disk) * {scale:Float64} DESC;

SELECT '=== does it fit the quota ===' AS section FORMAT TSVRaw;
-- {quota_gb:Float64} is the XFS project quota per ClickHouse data
-- directory. Headroom is what is left after the projection; a warehouse
-- planned to exactly fill its disk has no room for a merge, which needs
-- space for the new part before it can drop the old ones.
SELECT
    formatReadableSize(sum(bytes_on_disk) * {scale:Float64} / 2) AS per_node_projected,
    formatReadableSize({quota_gb:Float64} * 1024 * 1024 * 1024)  AS quota,
    round(100 * (sum(bytes_on_disk) * {scale:Float64} / 2)
          / ({quota_gb:Float64} * 1024 * 1024 * 1024), 1)        AS pct_of_quota,
    if(sum(bytes_on_disk) * {scale:Float64} / 2
       < {quota_gb:Float64} * 1024 * 1024 * 1024 * 0.7, 'ok', 'FAIL') AS verdict,
    '30% headroom' AS pass_line
FROM system.parts
WHERE database = 'nus' AND active AND rows > 0;

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
