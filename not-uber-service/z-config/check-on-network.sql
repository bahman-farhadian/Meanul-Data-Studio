-- Pickup, dropoff and stored route must sit on the connected street
-- network. A cab is not a boat: more than 75 m from every way is water,
-- a park, or an off-network island. A two-vertex route longer than 1 km
-- is the Euclidean chord, not a LION segment.
--
-- Uses KNN <-> against the gist on ways.the_geom, not ST_Collect of the
-- whole graph.

WITH recent AS (
    SELECT trip_id, pickup_point, dropoff_point, route, route_km
      FROM trips
     WHERE route IS NOT NULL
       AND ended_at > now() - interval '1 hour'
),
scored AS (
    SELECT r.*,
           ST_Distance(r.pickup_point::geography, pw.the_geom::geography) AS pickup_m,
           ST_Distance(r.dropoff_point::geography, dw.the_geom::geography) AS dropoff_m
      FROM recent r
      JOIN LATERAL (
            SELECT w.the_geom FROM ways w
             ORDER BY w.the_geom <-> r.pickup_point
             LIMIT 1
      ) pw ON true
      JOIN LATERAL (
            SELECT w.the_geom FROM ways w
             ORDER BY w.the_geom <-> r.dropoff_point
             LIMIT 1
      ) dw ON true
)
SELECT
    count(*) AS trips,
    count(*) FILTER (WHERE pickup_m > 75) AS pickup_off_network,
    count(*) FILTER (WHERE dropoff_m > 75) AS dropoff_off_network,
    count(*) FILTER (
        WHERE ST_NPoints(route) <= 2
          AND ST_Length(route::geography) > 1000
    ) AS long_two_point_routes
FROM scored;
