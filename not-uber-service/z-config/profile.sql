-- What the warehouse actually holds. Read by `make profile`.
--
-- The point of this file is that a dashboard cannot be planned from a schema.
-- A schema says a column exists; it does not say whether it is 90% NULL, has
-- one distinct value, or spans four orders of magnitude. Each of those facts
-- rules a chart in or out, so they are measured here rather than assumed.

SELECT '=== 1. is anything still arriving ===' AS section FORMAT TSVRaw;
SELECT * FROM (
    SELECT 'trip_events' AS tbl, count() AS rows, min(event_time) AS oldest,
        max(event_time) AS newest, dateDiff('minute', max(event_time), now()) AS behind_min
    FROM nus.trip_events
    UNION ALL SELECT 'driver_positions', count(), min(event_time), max(event_time),
        dateDiff('minute', max(event_time), now()) FROM nus.driver_positions
    UNION ALL SELECT 'rider_positions', count(), min(event_time), max(event_time),
        dateDiff('minute', max(event_time), now()) FROM nus.rider_positions
    UNION ALL SELECT 'hotspot_history', count(), min(computed_at), max(computed_at),
        dateDiff('minute', max(computed_at), now()) FROM nus.hotspot_history
) ORDER BY tbl;

SELECT '=== 2. trip_events — shape and cardinality ===' AS section FORMAT TSVRaw;
SELECT
    count()                                   AS rows,
    uniqExact(trip_id)                        AS trips,
    round(count() / uniqExact(trip_id), 2)    AS rows_per_trip,
    uniqExact(rider_id)                       AS riders,
    uniqExact(driver_id)                      AS drivers,
    uniqExact(pickup_zone_id)                 AS zones,
    uniqExact(toDate(event_time))             AS days
FROM nus.trip_events;

SELECT '=== 3. trip_events — the status dimension ===' AS section FORMAT TSVRaw;
SELECT status, count() AS rows, uniqExact(trip_id) AS trips,
       round(100 * count() / (SELECT count() FROM nus.trip_events), 1) AS pct_of_rows
FROM nus.trip_events GROUP BY status ORDER BY rows DESC;

SELECT '=== 4. trip_events — how many rows carry each measure ===' AS section FORMAT TSVRaw;
-- A measure that is NULL on most rows can only be charted on the subset that
-- has it, and the chart has to say so. This is that subset, per column.
SELECT
    count()                                        AS rows,
    countIf(route_km IS NOT NULL)                  AS has_route_km,
    countIf(predicted_duration_s IS NOT NULL)      AS has_predicted,
    countIf(actual_duration_s IS NOT NULL)         AS has_actual,
    countIf(duration_delta_s IS NOT NULL)          AS has_delta,
    countIf(surge_multiplier IS NOT NULL)          AS has_surge,
    countIf(hotspot_score IS NOT NULL)             AS has_hotspot_score,
    countIf(fare_estimate IS NOT NULL)             AS has_fare_estimate,
    countIf(fare_final IS NOT NULL)                AS has_fare_final,
    countIf(driver_id IS NOT NULL)                 AS has_driver
FROM nus.trip_events;

SELECT '=== 5. trip_events — the range of every measure ===' AS section FORMAT TSVRaw;
-- min / p50 / p95 / max decides axis scale, bucket width, and whether a
-- distribution is worth a histogram or collapses to a single bar.
SELECT 'route_km' AS measure, round(min(route_km),2) AS min, round(quantile(0.5)(route_km),2) AS p50,
       round(quantile(0.95)(route_km),2) AS p95, round(max(route_km),2) AS max, round(avg(route_km),2) AS mean
FROM nus.trip_events WHERE route_km IS NOT NULL
UNION ALL SELECT 'fare_final', round(min(fare_final),2), round(quantile(0.5)(fare_final),2),
       round(quantile(0.95)(fare_final),2), round(max(fare_final),2), round(avg(fare_final),2)
FROM nus.trip_events WHERE fare_final IS NOT NULL
UNION ALL SELECT 'fare_estimate', round(min(fare_estimate),2), round(quantile(0.5)(fare_estimate),2),
       round(quantile(0.95)(fare_estimate),2), round(max(fare_estimate),2), round(avg(fare_estimate),2)
FROM nus.trip_events WHERE fare_estimate IS NOT NULL
UNION ALL SELECT 'surge_multiplier', round(min(surge_multiplier),2), round(quantile(0.5)(surge_multiplier),2),
       round(quantile(0.95)(surge_multiplier),2), round(max(surge_multiplier),2), round(avg(surge_multiplier),2)
FROM nus.trip_events WHERE surge_multiplier IS NOT NULL
UNION ALL SELECT 'hotspot_score', round(min(hotspot_score),3), round(quantile(0.5)(hotspot_score),3),
       round(quantile(0.95)(hotspot_score),3), round(max(hotspot_score),3), round(avg(hotspot_score),3)
FROM nus.trip_events WHERE hotspot_score IS NOT NULL
UNION ALL SELECT 'predicted_duration_s', min(predicted_duration_s), quantile(0.5)(predicted_duration_s),
       quantile(0.95)(predicted_duration_s), max(predicted_duration_s), round(avg(predicted_duration_s),1)
FROM nus.trip_events WHERE predicted_duration_s IS NOT NULL
UNION ALL SELECT 'actual_duration_s', min(actual_duration_s), quantile(0.5)(actual_duration_s),
       quantile(0.95)(actual_duration_s), max(actual_duration_s), round(avg(actual_duration_s),1)
FROM nus.trip_events WHERE actual_duration_s IS NOT NULL
UNION ALL SELECT 'duration_delta_s', min(duration_delta_s), quantile(0.5)(duration_delta_s),
       quantile(0.95)(duration_delta_s), max(duration_delta_s), round(avg(duration_delta_s),1)
FROM nus.trip_events WHERE duration_delta_s IS NOT NULL;

SELECT '=== 6. trip_events — the two boolean flags ===' AS section FORMAT TSVRaw;
SELECT
    countIf(took_longer_than_predicted = 1) AS overran,
    countIf(took_longer_than_predicted = 0) AS on_time,
    countIf(is_hotspot_trip = 1)            AS from_hotspot,
    countIf(is_hotspot_trip = 0)            AS not_hotspot
FROM nus.trip_events;

SELECT '=== 7. the time dimension — is there a daily rhythm to plot ===' AS section FORMAT TSVRaw;
SELECT toHour(event_time) AS hour_utc, uniqExact(trip_id) AS trips,
       round(sumIf(fare_final, status='completed'), 0) AS revenue
FROM nus.trip_events GROUP BY hour_utc ORDER BY hour_utc;

SELECT '=== 8. the zone dimension — spread across zones ===' AS section FORMAT TSVRaw;
SELECT uniqExact(pickup_zone_id) AS zones,
       min(t) AS fewest_trips_in_a_zone, quantile(0.5)(t) AS median_zone, max(t) AS busiest_zone
FROM (SELECT pickup_zone_id, uniqExact(trip_id) AS t FROM nus.trip_events GROUP BY pickup_zone_id);
SELECT pickup_zone_id AS zone, uniqExact(trip_id) AS trips,
       round(sumIf(fare_final, status='completed'), 0) AS revenue,
       round(avgIf(surge_multiplier, status='completed'), 2) AS avg_surge
FROM nus.trip_events GROUP BY zone ORDER BY trips DESC LIMIT 8;

SELECT '=== 9. hotspot_history — its own dimensions ===' AS section FORMAT TSVRaw;
SELECT uniqExact(zone_id) AS zones, uniqExact(period) AS periods,
       groupUniqArray(period) AS period_values,
       round(min(demand_score),2) AS demand_min, round(quantile(0.5)(demand_score),2) AS demand_p50,
       round(max(demand_score),2) AS demand_max,
       round(min(surge_multiplier),2) AS surge_min, round(max(surge_multiplier),2) AS surge_max,
       max(open_requests) AS max_open_requests, max(available_drivers) AS max_free_drivers
FROM nus.hotspot_history;

SELECT '=== 10. driver_positions — the map and the fleet ===' AS section FORMAT TSVRaw;
SELECT status, count() AS rows, uniqExact(driver_id) AS drivers,
       round(avg(speed_kmh),1) AS avg_kmh, round(max(speed_kmh),1) AS max_kmh,
       countIf(trip_id IS NOT NULL) AS rows_with_trip
FROM nus.driver_positions GROUP BY status ORDER BY rows DESC;
SELECT round(min(lat),4) AS lat_min, round(max(lat),4) AS lat_max,
       round(min(lon),4) AS lon_min, round(max(lon),4) AS lon_max,
       uniqExact(zone_id) AS zones, countIf(heading_deg IS NOT NULL) AS has_heading,
       countIf(speed_kmh IS NOT NULL) AS has_speed
FROM nus.driver_positions;

SELECT '=== 11. rider_positions ===' AS section FORMAT TSVRaw;
SELECT count() AS rows, uniqExact(rider_id) AS riders, uniqExact(zone_id) AS zones,
       countIf(trip_id IS NOT NULL) AS rows_with_trip,
       countIf(accuracy_m IS NOT NULL) AS has_accuracy,
       round(quantile(0.5)(accuracy_m),1) AS accuracy_p50
FROM nus.rider_positions;

SELECT '=== 12. the rollup — confirm the per-node duplication factor ===' AS section FORMAT TSVRaw;
-- trip_stats_hourly is filled by a materialized view on each node, so the same
-- (hour, zone) exists once per shard. Every query must sum() first. If
-- rollup_trips and direct_trips match, the sum-then-divide rule is right.
SELECT
    (SELECT sum(completed_trips) FROM nus.trip_stats_hourly)                       AS rollup_trips,
    (SELECT count() FROM nus.trip_events WHERE status='completed')                 AS direct_trips,
    (SELECT round(sum(revenue),0) FROM nus.trip_stats_hourly)                      AS rollup_revenue,
    (SELECT round(sum(fare_final),0) FROM nus.trip_events WHERE status='completed') AS direct_revenue,
    (SELECT count() FROM nus.trip_stats_hourly)                                    AS rollup_rows,
    (SELECT uniqExact((hour, pickup_zone_id)) FROM nus.trip_stats_hourly)          AS distinct_hour_zone;
