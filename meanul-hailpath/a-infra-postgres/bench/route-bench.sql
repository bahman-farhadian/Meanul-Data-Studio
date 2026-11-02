-- Does batching the historical routing pass actually pay? One pgr_dijkstra
-- call per pair (what ships today) against one call for all the pairs at
-- once, over the same pairs, the same edge list and the same period.
--
-- Ramped 1 -> 10 -> 50 -> 200 -> 1000 on purpose. pgRouting builds its graph
-- in C++ memory that work_mem does not bound (routing.py's docstring, and
-- pgRouting issue #1319), so the ramp is the only honest way to see how
-- memory scales before anything large is asked for. Watch the container's
-- memory alongside this and stop if it climbs faster than the batch size.

\timing on
\set ON_ERROR_STOP on

-- Long enough for the whole ramp, short enough that a runaway aborts.
SET statement_timeout = '30min';

-- Scratch only. Dropped at the bottom of this file.
DROP TABLE IF EXISTS route_bench_pairs;
CREATE UNLOGGED TABLE route_bench_pairs (
    n      integer PRIMARY KEY,
    source bigint NOT NULL,
    target bigint NOT NULL
);

-- 1000 origin/destination pairs on the connected core, roughly 1-8 km
-- apart: the length band routing.py describes for a real New York trip.
-- The band is in degrees so it rides ways_vertices_pgr_main_geom_idx -
-- approximate at this latitude, which is fine for a timing comparison.
INSERT INTO route_bench_pairs (n, source, target)
SELECT row_number() OVER (), s.id, t.id
  FROM (
        SELECT id, the_geom
          FROM ways_vertices_pgr
         WHERE on_main_network
         ORDER BY random()
         LIMIT 1000
       ) s
  CROSS JOIN LATERAL (
        SELECT v.id
          FROM ways_vertices_pgr v
         WHERE v.on_main_network
           AND ST_DWithin(v.the_geom, s.the_geom, 0.072)
           AND NOT ST_DWithin(v.the_geom, s.the_geom, 0.009)
         ORDER BY random()
         LIMIT 1
       ) t;

SELECT count(*) AS pairs_built FROM route_bench_pairs;

DO $bench$
DECLARE
    edges_sql text := $edges$
        SELECT w.gid AS id, w.source, w.target,
               w.cost_s * COALESCE(st.congestion_factor, 1.0) AS cost,
               w.reverse_cost_s * COALESCE(st.congestion_factor, 1.0) AS reverse_cost
          FROM ways w
          LEFT JOIN segment_traffic st
                 ON st.way_id = w.gid AND st.period = 'morning'
    $edges$;
    rung        integer;
    t0          timestamptz;
    solo_ms     numeric;
    batch_ms    numeric;
    solo_rows   bigint;
    batch_rows  bigint;
BEGIN
    -- Warm shared_buffers so the first timed rung is not paying cold I/O.
    PERFORM count(*) FROM pgr_dijkstra(
        edges_sql,
        (SELECT source FROM route_bench_pairs WHERE n = 1),
        (SELECT target FROM route_bench_pairs WHERE n = 1),
        true);

    FOREACH rung IN ARRAY ARRAY[1, 10, 50, 200, 1000] LOOP
        t0 := clock_timestamp();
        SELECT count(d.seq) INTO solo_rows
          FROM route_bench_pairs p
          LEFT JOIN LATERAL pgr_dijkstra(edges_sql, p.source, p.target, true) d
                 ON true
         WHERE p.n <= rung;
        solo_ms := EXTRACT(epoch FROM clock_timestamp() - t0) * 1000;

        t0 := clock_timestamp();
        SELECT count(d.seq) INTO batch_rows
          FROM pgr_dijkstra(
                   edges_sql,
                   format('SELECT source, target FROM route_bench_pairs WHERE n <= %s', rung),
                   true) d;
        batch_ms := EXTRACT(epoch FROM clock_timestamp() - t0) * 1000;

        RAISE NOTICE
            'N=% | per-pair % ms total, % ms/pair, % rows | batched % ms total, % ms/pair, % rows | speedup %x',
            rung,
            round(solo_ms), round(solo_ms / rung, 1), solo_rows,
            round(batch_ms), round(batch_ms / rung, 1), batch_rows,
            round(solo_ms / greatest(batch_ms, 0.001), 1);
    END LOOP;
END
$bench$;

DROP TABLE route_bench_pairs;
