# Meanul Data Studio — Assessment Standard

This file is the assessment contract for Meanul Data Studio. Later versions
inherit §§1–4 unchanged. Version 1 (`not-uber-service`, NYC ride-hail) is
scored against this entire file. Other studio versions add a domain section
in the same form as §5; they do not replace this file.

A bar that cannot be scored is not a bar. Grafana “looks correct” is not a
bar. ClickHouse `axis_share` / `both_axes_pct` are not bars (the NYC street
grid is rotated from true north; those ratios do not prove street-follow or
Euclidean flight).

Numeric thresholds in §5 are copied from shipped sources. If this file and
those sources disagree, the sources win and this file is wrong. The pytest
`not-uber-service/z-lib/nus-common/tests/test_assessment_standard.py` fails
when the numbers or named `make` targets here drift from those sources.

---

## 1. How a bar is scored

Every bar has all four of:

1. **Name** — what is being measured.
2. **Instrument** — an existing `make` target, SQL file, pytest path, log
   field, or HTTP GET. Named with a backtick command in this file.
3. **Pass** — a numeric value, a required string, a required count, or
   “exit 0”.
4. **Fail** — the complementary value. Silence, a screenshot, or a ratio
   without a threshold is not a fail line.

Commands run from `not-uber-service/` unless noted. `make verify` is the
stack-health bundle. Simulation accuracy is `make verify-positions` plus
`make verify-walk`. Map tiles are `make tiles-health`.

---

## 2. Not bars

These exist in the repo and may be read for diagnosis. They do **not**
pass or fail a version:

| Signal | Why it is not a bar |
| --- | --- |
| Grafana map “looks like OSM” / “trails look right” | Visual only. Tiles are scored by HTTP in §5.7. Walks are scored by SQL and `path_p50` in §5.3–§5.4. |
| `axis_share`, `both_axes_pct` (`z-config/check-positions.sql`, `z-config/profile.sql` section 10b) | Manhattan streets are rotated ~29° from true north. ~0.71 is expected on a real polyline. |
| `make profile` section 10b | Same ratios as above. Section 1 of `make profile` (warehouse freshness) **is** a bar, §4 `n-service-clickhouse-sink`. |
| ClickHouse `Code 210` / HAProxy stats rows red because a node is the wrong Patroni role | Known noise. A node down in **both** pg_write and pg_read is the failure; see `make verify-pg`. |
| Redis db 3 = 0 immediately after `make up` | Live trip keys appear after dispatch. History is not required in db 3. |
| Postgres `trips` count << `HISTORY_DAYS × HISTORY_TRIPS_PER_DAY` after the archiver’s first ticks | Seeded history `ended_at` is yesterday. `ARCHIVER_RETENTION_HOURS` (default 24) deletes it from OLTP. ClickHouse `trip_events` keeps it. |

---

## 3. Studio-wide pipeline (every version)

These rules are domain-agnostic. A later version (streaming, flights, …)
keeps them and supplies its own §5 equivalent.

| # | Rule | Pass | Instrument |
| --- | --- | --- | --- |
| 3.1 | OLTP is the operational source of truth. Generators write profiles and durable state there. | Schema exists; `make verify-data` (or the version’s equivalent) prints non-zero entity counts after bootstrap. | `make verify-data` |
| 3.2 | Redis is the read path for services that are not the OLTP owner. Consumers do not join/lookup against PostgreSQL. | Cache-updater applies CDC; Redis db for the entity domain is non-zero after snapshot. Zero keys in that db after CDC means the generators must not have started work. | `make verify-data` Redis dbsize line; `make lag` group `cache-updater` |
| 3.3 | PostgreSQL changes reach Redis only via Debezium CDC over Kafka (`cdc.*` topics). | Connector state RUNNING, task RUNNING, slot active, `wal_status` reserved. | `make verify-cdc` |
| 3.4 | Cross-process events on Kafka are binary Avro with a schema registry. Topics the version declares have RF 3 and ISR 3. | `make verify-kafka` lists three replicas and three in-sync per partition. | `make verify-kafka`; schemas under the version’s Kafka piece |
| 3.5 | ClickHouse is the warehouse. Grafana and Superset query Distributed tables, never Kafka, Redis, Postgres, `cdc.*`, or `*_local`. | Cluster members present; `absolute_delay` at or near 0; dashboard check exit 0. | `make verify-ch`; version dashboard check (`make grafana-check` in v1) |
| 3.6 | Dashboards are not the generators’ source and are not the assessment of generation quality. | Live ops read warehouse tables; tile/HTTP and SQL bars below score the map and the sim. | `make grafana-health`; `make tiles-health` (if the version has same-origin tiles) |
| 3.7 | High-frequency position or playhead telemetry is not stored as OLTP ticks. OLTP may hold a last-known point. The stream is Kafka → warehouse. | No per-tick position table in OLTP migrations. Warehouse table for the stream exists and `make profile` section 1 `newest` is current. | OLTP migrations; `make profile` |
| 3.8 | Generators wait for a bootstrap-done marker before producing. The cache-updater does not wait (it is what fills the cache). | Redis key set; `make verify-data` prints the marker. | v1: `system:bootstrap:done` via `make verify-data` |
| 3.9 | `make up` does not rebuild images. A code change is `make build` then bring-up. Map/graph downloads are not required on every destroy. | Documented in the version Makefile help. | `make help`; `make build`; `make destroy` |
| 3.10 | Secrets are not in git. `.env` is untracked. | `.gitignore` lists `.env`. | `.gitignore` |

---

## 4. Component clauses

Each lettered piece of version 1, plus `z-config` / `z-lib`. A later version
keeps the same lettering for the same role or states the mapping in its
domain section.

### 4.1 `a-infra-postgres`

Patroni PostgreSQL (one Leader, two Replicas) and etcd.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Cluster shape | Exactly one Leader, two Replicas, lag at or near 0 MB | Two leaders, zero leaders, or replica lag growing | `make verify-pg` |
| Write path | Clients use HAProxy `5432` (write) / `5433` (read), never a `pg-*` hostname | Direct `pg-*` in a service’s `PG_HOST` | compose `PG_HOST` is `nus-lb-a` / `lb-a` |
| etcd after first start | Local `ETCD_INITIAL_CLUSTER_STATE=existing`; file not committed | `existing` committed to git | `a-infra-postgres/etcd.env` stays `new` in git; `make etcd-existing` is local |

### 4.2 `b-infra-redis`

Sentinel-managed cache. Logical DBs are fixed in `z-lib/nus-common/nus_common/redis_client.py`:

| DB | Constant | Keys |
| --- | --- | --- |
| 0 | `DB_SYSTEM` | `system:bootstrap:done` |
| 1 | `DB_DRIVER` | `driver:*`, `vehicle:*`, geo available sets |
| 2 | `DB_PASSENGER` | `passenger:*` |
| 3 | `DB_TRIP` | `trip:*`, `trip:*:active` |
| 4 | `DB_DEMAND` | `hotspot:*`, `zone:*` |

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Roles | One master, two slaves; Sentinel quorum reachable | Two masters, no quorum | `make verify-redis` |
| Cache fill after CDC | db 1 non-zero (drivers+vehicles); db 2 equals seeded passengers once snapshot has drained | db 1 still 0 after CDC | `make verify-data` Redis dbsize line |

### 4.3 `c-infra-kafka`

KRaft brokers, Schema Registry, Avro under `c-infra-kafka/schemas/`, topics in
`c-infra-kafka/topics/topics.tsv`. `cdc.*` are created by Debezium, not that
TSV.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Brokers and RF | Three brokers; declared topics RF 3, ISR 3 | ISR < 3 on a declared topic | `make verify-kafka` |
| ksqlDB | Server status RUNNING | Not RUNNING | `make verify-ksqldb` |

Version-1 declared topics: `driver_location`, `rider_location`,
`trip_requests`, `trip_lifecycle`, `city_hotspots`,
`segment_traffic_updates`.

### 4.4 `d-infra-debezium`

Connector `nus-pg`, slot `nus_debezium`, plugin `pgoutput`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Connector | `"state":"RUNNING"` on connector **and** task | Connector RUNNING with task FAILED | `make verify-cdc` |
| Slot | `nus_debezium` active `t`, `wal_status` `reserved` | Slot missing or inactive (WAL retains forever) | `make verify-cdc` |
| Geometry | `nus.trips.route` is in `column.exclude.list` | Geometry on a CDC topic (Debezium `DataException`) | `d-infra-debezium/connectors/nus-pg.json` |
| Topics | `cdc.drivers`, `cdc.passengers`, `cdc.trips`, `cdc.city_zones`, `cdc.vehicles`, `cdc.trip_ratings`, `cdc.driver_sessions` | `no cdc.* topics yet` after `make up` has finished `cdc-register` | `make verify-cdc` |

Patroni physical slots (`nus_pg_*`) are replica slots, not this bar.

### 4.5 `e-infra-clickhouse`

Cluster `nus` / `nus_cluster`: 2 shards × 2 replicas. Services and dashboards
query Distributed names (`nus.trip_events`, `nus.driver_positions`, …), never
`*_local`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Members | Four rows in `system.clusters` for `nus_cluster` | Fewer than four | `make verify-ch` |
| Tables | `nus` database lists Distributed + `*_local` + MVs from `e-infra-clickhouse/ddl/` | Missing `trip_events` or `driver_positions` | `make verify-ch` |
| Replication | `absolute_delay` at or near 0 | Delay only growing | `make verify-ch` |

### 4.6 `f-infra-grafana`

Grafana plus `nus-tiles` (piece f is both). Same-origin OSM at `/tiles/`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Process | Grafana `/api/health` database ok | Unhealthy | `make grafana-health` |
| Tiles file | `nyc.mbtiles` non-empty under `NUS_VOLUME_ROOT/nus-tiles-data` | Missing file | `make tiles-health` |
| Tiles container | `nus-tiles` health `healthy` | not running / unhealthy (HAProxy 503 HTML) | `make tiles-health` |
| Tiles HTTP | `GET /tiles/styles.json` → 200; `GET /tiles/styles/nus/11/602/768.png` → 200 `image/png` | 503 HTML, 404, or non-PNG | `make tiles-health` |
| Dashboards | Geomap URL contains `/tiles/styles/nus/` and `"type": "xyz"`; no `maptiler` / `cartocdn.com`; panels use uid `nus-clickhouse` and Distributed tables | Third-party tile CDN or `*_local` | `make grafana-check` |

`make up` starts `grafana` and `tiles` (`PIECE_F`). `make destroy` keeps
`nus-tiles-data`. `make nuke` is what deletes the MBTiles.

### 4.7 `g-infra-superset`

Analytical BI on the same ClickHouse cluster. Empty panels before data are
fine; an HTTP error is not.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Health | `/health` OK | Error page | `make superset-health` |

### 4.8 `h-bootstrap`

One-shot: migrations, LION restore, people, history, warehouse load, then
`system:bootstrap:done`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Marker | Redis `system:bootstrap:done` is `1` | `(not set)` | `make verify-data` |
| OLTP seed | `drivers` = `SEED_DRIVERS`; `passengers` = `SEED_PASSENGERS`; `city_zones` = 263 (TLC); `ways` in the hundreds of thousands | `ways` = 0 (`SKIP_MAP_IMPORT` or failed restore) | `make verify-data` |
| Warehouse seed | After bootstrap, `nus.trip_events` count is at least `HISTORY_DAYS × HISTORY_TRIPS_PER_DAY` (one warehouse row per seeded trip). Live traffic then adds lifecycle rows. | 0 events after a finished `make up` | `make verify-data` |
| History routes | Seeded trips that have a `route` are on-network (same 75 m / long two-point bars as §5.3), produced by `nus_common.routing.route()` | Unsnapped harbour points or two-point chords in history | `z-config/check-on-network.sql` (window is last hour for live; all-time `route IS NOT NULL` is the history form) |

### 4.9 `i-service-cache-updater`

Applies `cdc.*` to Redis. Does not wait for bootstrap.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Lag | `make lag` group `cache-updater`: LAG near 0 after snapshot drain | LAG only growing | `make lag` |
| Keys | db 1 and db 2 non-zero after snapshot | db 1 = 0 | `make verify-data` |

### 4.10 `j-service-driver`

One process, many drivers. Positions go to Kafka `driver_location`, not a
Postgres tick table. `drivers.last_lat` / `last_lon` are last-known, synced
on a timer.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Movement | `follow()` walks `linestring_vertices` of Redis WKT or `routing.drive_path`; empty path and `is_chord_path` path do not lerp the chord | Euclidean hop between two ends | `make verify-walk` (`j-service-driver/tests`) |
| Live polyline | Latest tick: `path_p50` is not 2 while `online` > 0. `path_chord` is the count of online paths with ≤ 2 vertices (short real LION blocks allowed). | `path_p50` = 2 with a live fleet | `docker logs driver-service` tick JSON (`path_p50`, `path_chord`); `make verify-positions` |
| Stream | `nus.driver_positions` `newest` within a minute of now on a live stack | Table empty while the service is up | `make profile` section 1 |

### 4.11 `k-service-passenger`

Riders. Independent of driver-service. Requests on `trip_requests`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Cache wait | Process waits for `passenger:*` then produces | Group missing forever while db 2 is filled | `make lag` group `passenger-service`; `make verify-data` db 2 |
| Demand | `trip_requests` LOG-END-OFFSET increases at about `TRIP_REQUESTS_PER_MINUTE` | Topic stays 0 after passenger-service is running | `make lag` group `dispatch-service` on `trip_requests` |

### 4.12 `l-service-dispatch`

Match, pgRouting, fare, trip status. **Redis `trip_active` (route WKT)
before Kafka announce.**

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Order | `store_live_state` then `announce` on match and on each non-terminal status | Kafka `matched` / `in_progress` with empty `trip:{id}:active` (driver skips `follow()`, walks an idle chord) | Source `l-service-dispatch/dispatch_service/__main__.py`; live: `make verify-positions` |
| On-network assignment | Last-hour trips with `route IS NOT NULL`: `pickup_off_network` = 0, `dropoff_off_network` = 0, `long_two_point_routes` = 0; `route_km` > `chord_km` when `trips` > 0 | Any off-network count > 0, or long two-point routes > 0 | `make verify-positions` |

### 4.13 `m-service-city`

Hotspots and `segment_traffic` updates.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Process | Container not restart-looping; lag on `driver_location` may be tens of seconds, must not only grow | Restart loop; lag only growing | `make lag` group `city-service`; `make errors` |
| Warehouse | `nus.hotspot_history` `newest` current on a live stack | Empty while city-service is up | `make profile` section 1 |

`hot_zones: 0` at 40 req/min over 263 zones is expected (0.6 cutoff). Not a fail.

### 4.14 `n-service-clickhouse-sink`

Kafka → Redis enrich → ClickHouse Distributed tables.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Freshness | `make profile` section 1: `trip_events` and `driver_positions` `behind_min` small on a live stack | `newest` stuck | `make profile` |
| Lag | `make lag` group `clickhouse-sink` returns toward 0 after a burst | Lag only growing | `make lag` |
| Live rows | After live traffic, `rows_per_trip` on `trip_events` > 1 (lifecycle walk). Bootstrap alone is one row per seeded trip. | Live stack with `rows_per_trip` = 1 forever | `make profile` section 2 |

### 4.15 `o-service-archiver`

Deletes OLTP trips with `ended_at` older than `ARCHIVER_RETENTION_HOURS`.
Analytics remain in ClickHouse.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Backlog | `should_have_been_pruned` = 0: `ended_at IS NOT NULL AND ended_at < now() - ARCHIVER_RETENTION_HOURS` | Growing leftover terminal trips | `make psql` (write proxy): `SELECT count(*) FROM trips WHERE ended_at IS NOT NULL AND ended_at < now() - interval '24 hours';` (use the configured hours) |

### 4.16 `z-config`

Stack HAProxy, assessment SQL, `profile.sql`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Tiles routing | Frontend `grafana_fe` ACL `path_beg /tiles/` → backend `tiles`; rewrite `^/tiles/(.*) /\1` is on the **backend**, not the frontend | Strip on the frontend (tiles 302/404 from Grafana) | `z-config/haproxy/haproxy.cfg.template`; `make tiles-health` |
| On-network SQL | `z-config/check-on-network.sql` is what `make verify-positions` runs | Different thresholds in this file than in that SQL | §5.3 and `test_assessment_standard.py` |
| Positions SQL | `z-config/check-positions.sql` is diagnostic (not a pass/fail bar) | Using `axis_share` as a gate | §2 |

### 4.17 `z-lib`

`nus-common`: ids, geo, routing, Redis keys, clients.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| IDs | `drv-` width 11, `psg-` width 11, `trp-` width 21 | ClickHouse `FixedString` rejects the id | `z-lib/nus-common/nus_common/ids.py`; `make verify-walk` |
| Chord | `is_chord_path(..., min_km=0.2)` is true iff the path has exactly two points ≥ 0.2 km apart | Treating a short two-vertex LION block as a chord | `make verify-walk` |
| Route geometry | `ROUTE_GEOMETRY_SQL` orients edges (`ST_Reverse` when traversed target→source) then `ST_MakeLine` in visit order | `ST_LineMerge(ST_Collect)` without path order | `z-lib/nus-common/nus_common/routing.py`; live `make verify-positions` |

---

## 5. Version 1 — NYC ride-hail simulation accuracy

These bars are the soul of version 1. They are copied from shipped SQL and
Python. They are not interpretations of Grafana.

### 5.1 Generation

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Bootstrap finished | `system:bootstrap:done` = 1 | Marker unset; generators waiting | `make verify-data` |
| People | `drivers` = `SEED_DRIVERS`; `passengers` = `SEED_PASSENGERS` | Counts 0 | `make verify-data` |
| Map | `ways` in the hundreds of thousands | `ways` = 0 | `make verify-data` |
| History volume | Warehouse `trip_events` ≥ `HISTORY_DAYS × HISTORY_TRIPS_PER_DAY` after `make up` | 0 | `make verify-data` |
| Live demand | `trip_requests` offsets increase; last-hour completed trips in Postgres are > 0 after several minutes | No live trips while passenger-service is up | `make lag`; `make verify-positions` (`trips` column) |

Default small seed used to prove this file: `SEED_DRIVERS=800`,
`SEED_PASSENGERS=5000`, `HISTORY_DAYS=1`, `HISTORY_TRIPS_PER_DAY=2000`,
`TRIP_REQUESTS_PER_MINUTE=40.0`, `SKIP_MAP_IMPORT=false`. Those values are
`.env` knobs, not additional bars. The bars above compare the store to
whatever those knobs are set to.

### 5.2 Identifiers

From `z-lib/nus-common/nus_common/ids.py` (ClickHouse `FixedString` matches):

| Entity | Prefix / shape | Length |
| --- | --- | --- |
| Driver | `drv-` + 7 digits (`drv-0000001`) | 11 |
| Passenger | `psg-` + 7 digits (`psg-0000001`) | 11 |
| Trip | `trp-` + `YYYYMMDD` + `-` + 8 hex (`trp-20250824-a1b2c3d4`) | 21 |

`zone_id` is TLC LocationID (`"1"`..`"263"`), not minted here.

Pass: ids in Postgres, Redis, Kafka Avro, and ClickHouse match those
shapes. Fail: a write error on `FixedString` or a mixed prefix.

Instrument: `make verify-walk` (unit); warehouse `make profile` (live
cardinality).

### 5.3 On-network (a cab is not a boat)

From `z-config/check-on-network.sql`, run by `make verify-positions`, on
trips with `route IS NOT NULL` and `ended_at > now() - interval '1 hour'`:

| Column | Pass | Fail |
| --- | --- | --- |
| `pickup_off_network` | 0 | `ST_Distance(pickup_point, nearest ways.the_geom) > 75` m |
| `dropoff_off_network` | 0 | same, dropoff, **75** m |
| `long_two_point_routes` | 0 | `ST_NPoints(route) <= 2` **and** `ST_Length(route::geography) > 1000` m (1 km) |

Also printed by the same target: `route_km` (mean `ST_Length(route)`) must
be greater than `chord_km` (mean pickup–dropoff distance) when `trips` > 0.
`avg_vertices` is tens or hundreds, not 2.

A last-hour `trips` = 0 is “no live completed trips yet”, not a pass. Wait
and re-run `make verify-positions`.

### 5.4 Chord vs street walk

From `z-lib/nus-common/nus_common/geo.py`:

```
is_chord_path(path, min_km=0.2)
```

True only when `len(path) == 2` and the geodesic distance is **≥ 0.2 km**.
A two-vertex LION block shorter than 0.2 km is a real street segment, not a
chord.

Live motion:

- Driver installs `linestring_vertices(wkt)` from Redis `trip_active`
  (`route_wkt` / `pickup_route_wkt`).
- If that path is empty or `is_chord_path`, it calls `routing.drive_path`
  (pgRouting). `drive_path` returns `[]` rather than a two-point hop when
  routing fails.
- `follow([])` does not move. `become_idle()` clears the polyline.

Pass: `make verify-walk` exit 0; live tick `path_p50` ≠ 2 while `online` > 0;
§5.3 all zeros.

Fail: `path_p50` = 2 on a live fleet; `long_two_point_routes` > 0;
`verify-walk` non-zero.

Instrument: `make verify-walk`; `make verify-positions`; driver-service
tick fields `path_chord` and `path_p50`.

### 5.5 Redis `trip_active` before Kafka

Dispatch writes `trip:{trip_id}:active` (`redis_client.trip_active_key`)
**before** `announce()` on Kafka `trip_lifecycle`. The JSON includes route
WKT. Driver-service reads that key to `follow()`.

Pass: source order `store_live_state` then `announce`; live §5.3–§5.4 pass
(the historical failure mode was Kafka first → empty key → idle chord).

Instrument: `l-service-dispatch/dispatch_service/__main__.py`;
`make verify-positions`.

### 5.6 Positions are not OLTP ticks

There is no Postgres `driver_positions` table. Migrations create
`drivers.last_lat` / `last_lon` (last known). Ticks are Avro
`driver_location` → `nus.driver_positions` (ClickHouse), TTL on the
warehouse table.

Pass: `make profile` section 1 shows `driver_positions` arriving;
`h-bootstrap/migrations/` has no per-tick position table.

### 5.7 Time

Stored timestamps are UTC (`TZ=UTC`, ClickHouse `DateTime64(3, 'UTC')`).
Demand, `day_period`, and TLC `zone_weight` use `SIM_TIMEZONE`
(version-1 default `America/New_York`) via `nus_common.geo.in_sim_tz` /
`sim_zoneinfo`.

Pass: compose injects `SIM_TIMEZONE` on generator services; images have
`tzdata`. Fail: `ZoneInfo` silently UTC because slim images lack zoneinfo.

Instrument: compose `environment` on `h-bootstrap`, driver, passenger,
dispatch, city, clickhouse-sink; `z-lib/nus-common/nus_common/geo.py`.

### 5.8 Same-origin tiles, not MapTiler

Browser URL: `/tiles/styles/nus/{z}/{x}/{y}@2x.png` (and the 1× PNG).
HAProxy on `LB_A_GRAFANA_PORT` (default 3000) strips `/tiles/` on the tiles
backend and forwards to `nus-tiles:8080`.

| GET | Pass |
| --- | --- |
| `/tiles/styles.json` | HTTP 200 |
| `/tiles/styles/nus/11/602/768.png` | HTTP 200, `Content-Type` `image/png` |

Fail: HTTP 503 body `No server is available to handle this request`
(`nus-tiles` down); MapTiler “API KEY REQUIRED”; `maptiler` in dashboard
JSON.

Instrument: `make tiles-health`; `make grafana-check`.

---

## 6. Command index

Assessment commands named in this file (must exist as Makefile targets):

| Command | What it scores |
| --- | --- |
| `make verify` | §§4.1–4.8 bundle (pg, redis, kafka, ksqldb, cdc, ch, dash, data) |
| `make verify-pg` | §4.1 |
| `make verify-redis` | §4.2 |
| `make verify-kafka` | §4.3 |
| `make verify-ksqldb` | §4.3 |
| `make verify-cdc` | §4.4 |
| `make verify-ch` | §4.5 |
| `make grafana-health` | §4.6 |
| `make tiles-health` | §4.6, §5.8 |
| `make grafana-check` | §4.6, §5.8 |
| `make superset-health` | §4.7 |
| `make verify-data` | §3.1, §3.8, §4.8, §5.1 |
| `make verify-dash` | grafana + tiles + superset |
| `make verify-positions` | §4.12, §5.3, §5.4, §5.5 |
| `make verify-walk` | §4.10, §5.2, §5.4 |
| `make lag` | §3.2, §4.9, §4.11, §4.13, §4.14 |
| `make profile` | §3.7, §4.10, §4.13, §4.14, §5.6 |
| `make help` | §3.9 |
| `make build` | §3.9 |
| `make destroy` | §3.9, §4.6 (keeps LION + OSM tiles) |
| `make psql` | §4.15 |
| `make errors` | §4.13 |

SQL and pytest the bars cite:

| Path | Role |
| --- | --- |
| `z-config/check-on-network.sql` | 75 m, `ST_NPoints` ≤ 2, 1000 m |
| `z-config/check-positions.sql` | Diagnostic only (§2) |
| `z-config/profile.sql` | Warehouse freshness and ranges |
| `z-lib/nus-common/nus_common/geo.py` | `is_chord_path` `min_km=0.2` |
| `z-lib/nus-common/nus_common/ids.py` | `drv-` / `psg-` / `trp-` |
| `z-lib/nus-common/tests/` | `make verify-walk` |
| `j-service-driver/tests/` | polyline walk vs chord |

---

## 7. Inheritance

A new studio version:

1. Keeps §§1–4 (map component letters if the directory names change).
2. Adds a domain section with the same table shape as §5: named instrument,
   named pass, named fail, numbers taken from that version’s shipped SQL or
   tests — not from a screenshot.
3. Extends `test_assessment_standard.py` (or the version’s equivalent) so
   those numbers cannot drift.

Version 1 is closed for this file when `make verify`, `make tiles-health`,
`make verify-walk`, and `make verify-positions` (last-hour `trips` > 0)
all pass the tables above.
