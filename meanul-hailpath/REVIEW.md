# Hailpath stack review

The one-host loop is up (CDC, cache, driver, passenger, dispatch, city,
sink, archiver). That is not the same as the warehouse and the front end
being right. Live facts below were read from this laptop on 2026-11-20.

Work these in order. One issue per task. Do not mix a DDL change with a
Grafana rewrite.

---

## ClickHouse

### CH-1. The `hailpath` user opens `default`
`SELECT currentDatabase()` as `hailpath` returns `default`. Compose never
sets `CLICKHOUSE_DB`. DBeaver and `clickhouse-client` land in an empty
database. Tables live in `hailpath`.
**Do:** Set `CLICKHOUSE_DB=hailpath` on the server (the image supports it).
Confirm `currentDatabase()` is `hailpath`. Recreate the container if the
user was created on first start without it.

### CH-2. Database `default` is still there
`SHOW DATABASES` lists `default` next to `hailpath`. `default` has no
tables. The official image always creates it. The `hailpath` login can
still `USE default` and has grants on `*.*`.
**Do:** Stop granting `*.*`. Give `hailpath` only `hailpath.*`. Empty
`<networks>` on the image `default` user is not enough; lock that user
out of the product.

### CH-3. `hailpath` is a superuser
Live `SHOW GRANTS`: `FILE`, `S3`, `URL`, `SYSTEM`, `DROP`, `CLUSTER` on
`*.*`. Grafana and the sink need SELECT/INSERT on `hailpath`.
**Do:** Replace image-generated grants with a `users.d` profile: SELECT
(Grafana), INSERT (sink), CREATE only for `ch-ddl-init`. No FILE/S3/URL.

### CH-4. Rollup columns are `UInt64` for counts that are tens
`trip_stats_hourly.completed_trips` max is 10. `dispatch_funnel_hourly.offers_made`
max is 29. Same `UInt64` on every SummingMergeTree count. Even 655k trips
per day fits in `UInt32`.
**Do:** Change those count columns to `UInt32` in `ddl/005`–`011`. Recreate
the rollup tables (they are derived). Leave raw event tables alone unless
a column is actually wrong.

### CH-5. DDL files are a journal
`002_positions.sql` spends most of its lines on 79 GiB, monthly parts, and
`make capacity`. `003` and `010` do the same. The running schema is hard
to read. Comments describe a 106k-driver disk plan as if it were this
laptop.
**Do:** Keep one line on engine, partition, TTL. Move the measurement
notes out of `ddl/`.

### CH-6. Too many rollups for what Grafana reads
29 objects in `hailpath` (tables + MVs). Grafana SQL hits about 14 names.
`trip_duration_percentiles_hourly`, `od_matrix_daily`, `active_entities_hourly`
are barely used. Each MV writes on every insert on a 2g cgroup.
**Do:** Keep `trip_events`, positions, `hotspot_history`, `segment_traffic_history`,
`dispatch_offers`, plus the rollups the home dashboard actually queries.
Drop or stop building the rest until a panel needs them.

### CH-7. Image still writes `users.d/default-user.xml`
The entrypoint creates a `default` user file with a password even when
the product user is `hailpath`.
**Do:** Follow current ClickHouse image docs: disable `default` explicitly
in a mounted `users.d` file. Do not rely on empty `<networks>`.

---

## Grafana

### GF-1. Live-ops is a scan, not a dashboard
Home dashboard refresh is 10s. “Open trips by last status” does
`argMax(status, event_time)` over 6 hours of `trip_events` grouped by
`trip_id`. Six of nine live-ops panels have empty `description`. Timezone
is `America/New_York` while the stack is UTC.
**Do:** One home page: ingest, lag, open trips, map. Query a 15-minute
window or a last-status table. Refresh 30s. Timezone UTC. Fill descriptions.

### GF-2. Six dashboards, 46 panels, 15 with no description
Marketplace is 14 KPIs. History is 10. Trip inspector needs template
variables and is quiet without a `trip_id`. The front end does not answer
one question first.
**Do:** Home = live-ops (cut). Keep driver / trip / city as drill-down.
Cut marketplace/history to what you actually watch. Every remaining panel
gets a one-line description.

### GF-3. Maps are OSM, not LION
Geomap XYZ is `hailpath-tiles` OSM on `:8080`. Routing uses LION `ways`.
The map and the router disagree.
**Do:** Later piece: serve LION as the Grafana basemap. Do not keep OSM
once LION tiles exist.

### GF-4. Panels that “return rows” still fail as a product
`make verify-grafana` counts any row as success. A trip inspector panel
can be empty until a variable is set. Live-ops can be green while the
SQL is unaffordable on a laptop.
**Do:** Change the bar: named panels must return the metric you care
about (lag seconds, trips/min), not “rows > 0”.

---

## Services

### SV-1. City was behind `driver_location` by thousands
1060 drivers send ~6700 positions per tick. City used to stop after 5000
polls (`poll_once(0)` treated an empty buffer as EOF). Lag grew. Drain
was changed to a time budget (`9e69f7c`, `d5662e6`). Still 0.5 CPU vs 12
partitions of the heaviest topic.
**Do:** Keep the drain fix. Score demand from a sample or last-per-driver,
not every position, if lag grows again.

### SV-2. Debezium is at 94% of 640m
RSS ~605 MiB / 640m. Snapshot and lag catch-up will OOM.
**Do:** Raise `DEBEZIUM_MEM` a little or drop heap so RSS stays under ~70%
of the cgroup. Do not leave 94% as “healthy”.

### SV-3. Sink comment still says “cluster”
`clickhouse_sink/__main__.py` talks about turning a “healthy cluster into
a merge queue”. There is one ClickHouse.
**Do:** Fix the comment when that file is next touched. Inserts are still
batched.

### SV-4. `redis_client.primary()` is leftover naming
`client()` exists. Driver, passenger, dispatch, city, sink, archiver still
call `primary()`.
**Do:** Rename at each service when that piece is edited. Do not do it as
a pile of drive-by diffs.

### SV-5. Connect REST is published with no login
`:8083` on `0.0.0.0`. Anyone on the LAN can PUT a connector.
**Do:** Bind Connect to `127.0.0.1` on the laptop, or stop publishing it.
Product traffic is on `hailpath-net`.

---

## Ops / leftover

### OP-1. `make kafka-certs` rewrites the CA on every `make up`
A running broker keeps the old cert in memory. New clients fail until
recreate.
**Do:** Generate only when the secret files are missing.

### OP-2. TTL and comments are sized for Dionysus
Position TTL comments talk 53 GiB. This host is `CH_MEM=2g`.
**Do:** Keep deploy numbers in `.env.example` comments labelled deploy.
Laptop TTL can stay short; do not describe 106k disk as current.

### OP-3. Enum8 `arrived = 10`
Status was appended at the end so old numbers stay valid. The next status
cannot be inserted in lifecycle order.
**Do:** Either live with append-only Enum8 and document it, or store
status as `LowCardinality(String)` matching Postgres text.

---

## Suggested order

1. CH-1, CH-2, CH-3 (login and grants).  
2. CH-4, CH-5, CH-6 (types, readable DDL, fewer MVs).  
3. GF-1, GF-2, GF-4 (home dashboard that is usable).  
4. SV-2, SV-5, OP-1 (will not die or leak).  
5. GF-3 LION map.  
6. SV-1, SV-4, OP-2, OP-3 as those files are touched.
