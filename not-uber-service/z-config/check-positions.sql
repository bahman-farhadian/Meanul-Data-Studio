-- Live positions should follow streets, not the chord between two points.
-- axis_share near 1.0 = grid-aligned; near 0.71 = flying. Run on the last
-- 5 minutes so a rebuild is not mixed with the old Euclidean samples.

WITH seq AS (
    SELECT
        abs(lat - lagInFrame(lat, 1) OVER (PARTITION BY driver_id ORDER BY event_time)) * 111000 AS dn,
        abs(lon - lagInFrame(lon, 1) OVER (PARTITION BY driver_id ORDER BY event_time)) * 85000 AS de
    FROM nus.driver_positions
    WHERE event_time >= now() - INTERVAL 5 MINUTE
)
SELECT
    countIf(dn + de > 8) AS steps,
    round(avgIf(greatest(dn, de) / (dn + de), dn + de > 8), 3) AS axis_share
FROM seq;
