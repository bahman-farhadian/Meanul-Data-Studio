-- Live positions should follow streets, not the chord between two points.
-- axis_share = max(|dN|,|dE|) / (|dN|+|dE|) on steps of at least 8 m.
--   1.00 = one axis (Manhattan grid)
--   0.50 = 45-degree hop
--   ~0.70 = Euclidean idle lerp across a city that is wider than tall
-- both_axes_pct = share of steps that cut a block (both axes > 8 m).

WITH seq AS (
    SELECT
        status,
        abs(lat - lagInFrame(lat, 1) OVER (PARTITION BY driver_id ORDER BY event_time)) * 111000 AS dn,
        abs(lon - lagInFrame(lon, 1) OVER (PARTITION BY driver_id ORDER BY event_time)) * 85000 AS de
    FROM nus.driver_positions
    WHERE event_time >= now() - INTERVAL 5 MINUTE
)
SELECT
    status,
    countIf(dn + de > 8) AS steps,
    round(avgIf(greatest(dn, de) / (dn + de), dn + de > 8), 3) AS axis_share,
    round(100.0 * countIf(dn > 8 AND de > 8) / nullIf(countIf(dn + de > 8), 0), 1) AS both_axes_pct
FROM seq
GROUP BY status
ORDER BY steps DESC;
