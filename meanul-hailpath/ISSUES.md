# Hailpath issues

Single open-work list. Measured 2026-11-21 on this laptop after
`make destroy` then `make build && make up` (`SEED_DRIVERS=1060`,
ClickHouse cgroup 2g). The loop runs with integer keys.

Work top to bottom. Schema first, then the services that fill it. One
issue per change.

**Live 2026-11-21.** Integer keys, last-known GPS, ReplacingMergeTree,
CDC include-list, Connect loopback, cert skip, Grafana live-ops
UTC/30s/15m, `CLICKHOUSE_DB=hailpath`, grants on `hailpath.*` only,
`quality.sql` without `uniqExact` on positions. City scores from Redis
GEO and `driver_last_known`, not `driver_location`. Debezium cgroup
1280m, live RSS 46%.

Still open: unused warehouse MVs (2.4), leftover rollup journals (2.5),
panel-probe `rows > 0` (4.2), LION tiles (4.3).

---

## 1. ClickHouse schema — keys and types as stored

### 1.1 `trip_id` is `FixedString(21)` (Postgres `text`)
Was live sample `trp-20261118-01xleNJo`. 21 bytes on every join key.
A `UInt64` is 8 bytes and compares in one register.

**Do:** Mint `bigint` / `UInt64` in `ids.py`, Avro `long`, Postgres
`bigint`, ClickHouse `UInt64`, Redis int. Format a display string only
in logs.

**Status:** done. Live `trip_events.trip_id` sample `202611190005556`
(`UInt64` = `YYYYMMDD * 10_000_000 + seq`). PG `bigint`. Avro `long`.

### 1.2 `driver_id` / `passenger_id` / `vehicle_id` are formatted ints
Was live `drv-0000180`, `psg-0005313`. Stored `FixedString(11)` / PG
`text`.

**Do:** Store `integer` / `UInt32`. Print `drv-` / `psg-` / `veh-` only
when printing.

**Status:** done. Live `driver_last_known.driver_id` 1..5 `UInt32`. PG
`drivers.driver_id` / `vehicles.vehicle_id` `integer`. Redis
`driver:526`, `vehicle:168`.

### 1.3 `zone_id` is text for TLC 1..263
Was CH `LowCardinality(String)`, PG `text`, Avro `string`. Cardinality
is 263.

**Do:** `UInt16` in PG, Avro, CH. Parse TLC LocationID at ingest.

**Status:** done. Live CH `UInt16`, PG `smallint`, Avro `int`. Sample
pickup zones 184, 181, 260. `city_zones.zone_id` 1 = Newark Airport.

### 1.4 `event_id` is UUID (16 bytes) on every fact row
Was on `driver_positions`, `rider_positions`, `trip_events`,
`dispatch_offers`, `hotspot_history`, `segment_traffic_history`,
`trip_facts`. 16 bytes × 12.39 million GPS rows.

**Do:** Stop storing UUID on position rows. Keep a cheaper idempotency
key (offset, or `UInt64`) if the sink still needs one.

**Status:** done. Live `driver_positions` / `driver_last_known` have no
`event_id`. `trip_events` and `dispatch_offers` still use UUID
`event_id` as the ReplacingMergeTree version key.

### 1.5 Rollup counts are `UInt64` for values of 10–29
Was `trip_stats_hourly.completed_trips` max = 10.

**Do:** `UInt32` when those tables are rebuilt with 1.1–1.3.

**Status:** done. SummingMergeTree count columns are `UInt32`.
`SimpleAggregateFunction(sum, …)` stores `UInt64` because ClickHouse
`sum()` returns `UInt64` (Code 36 if stored as `UInt32`).

---

## 2. ClickHouse tables, size, correctness

### 2.1 `driver_positions` is the warehouse
Was **12,388,084** rows, 92.8% idle `trip_id` NULL, Grafana folding
that table every 10s.

**Do:** Integer `driver_id`. Do not store `trip_id` on idle ticks. Give
Grafana a last-known table, not 12M raw ticks.

**Status:** done. Live `driver_positions` **35,859** rows (history with
a trip). `driver_last_known` **1,647** rows (ReplacingMergeTree,
~1060 drivers). Grafana map reads last-known. Kafka `driver_location`
is the GPS firehose; MergeTree is not.

### 2.2 `hotspot_history` is a 30-second snapshot dump
Was 570,624 rows; city published every zone every 30s.

**Do:** Write a row when the score changes, or at 5 minutes. Redis
already holds the current value.

**Status:** done. Live `hotspot_history` **6,656** rows (seed 6,144 plus
a short live window). City log: most 30s ticks `kafka_rows=0`; one
5-minute dump `kafka_rows=256`; then 1. Kafka `city_hotspots` grew
**1** message in 30s (513 → 514), not 256.

### 2.3 Duplicate `event_id` on `trip_events`
Was 29,803 rows, 669 extra. `uniqExact` on `driver_positions` OOM.

**Do:** ReplacingMergeTree on `event_id`. Stop `uniqExact` on
`driver_positions` in `quality.sql`.

**Status:** done. Live `trip_events` 6,592 rows, `count() - uniq(event_id)
= 0`. `dispatch_offers` extra = 0.

### 2.4 Twenty-nine objects; Grafana uses about fourteen
29 tables/MVs in `hailpath`. Dashboard JSON references ~14 names.
Every insert fans into MVs on 2g RAM.

**Do:** Raw facts + the rollups the home dashboard reads. Add MVs when a
panel needs them.

**Status:** open. Home now reads `trip_last_status` and
`driver_last_known`. Unused MVs were not dropped in this pass.

### 2.5 DDL files are a capacity journal
`002_positions.sql` used to be 79 GiB / monthly-part history for a 106k
fleet. This host is `CH_MEM=2g`.

**Do:** Engine, partition, TTL. One line each. Measurements out of
`ddl/`.

**Status:** partial. Position, trip, and `012_position_rollups.sql`
comments were cut. Some other rollup files still carry measurement
notes.

### 2.6 Login opens database `default`
Was `currentUser(), currentDatabase()` → `hailpath`, **`default`**.

**Do:** `CLICKHOUSE_DB=hailpath` on the server. Confirm
`currentDatabase() = hailpath`.

**Status:** done with a limit. Compose sets `CLICKHOUSE_DB=hailpath`;
the database exists; Grafana and `make ch-client` open it.
`SELECT currentUser(), currentDatabase()` as user `hailpath` with no
`--database` is still `hailpath` / `default` because the image always
keeps an empty `default` database. Setting `<default_database>hailpath`
in `grants.xml` makes the image's first-boot `CREATE DATABASE` fail
(the user cannot log in until that database exists). Do not put it
back.

### 2.7 User `hailpath` has `GRANT … ON *.*`
Was FILE, S3, URL, DROP, SYSTEM, CLUSTER on every database.

**Do:** `users.d`: only `hailpath.*`. The image entrypoint already
removes the unnamed `default` user when `CLICKHOUSE_USER=hailpath`.

**Status:** done. Live `SHOW GRANTS`: SELECT/INSERT/ALTER/CREATE/DROP/
TRUNCATE/SHOW **ON hailpath.*** only. Re-declaring `<default>` in
`grants.xml` without an auth method made ClickHouse 26.8 refuse to
start (SAX / BAD_ARGUMENTS). The image's `default-user.xml` already
deletes that user.

---

## 3. Services (they produce this schema)

### 3.1 Driver last-seen sync every 30s → CDC storm
Was ~600 dirty rows / 30s → `cdc.drivers` 1.3M messages for 1060
driver rows.

**Do:** Persist last-seen on a long timer (minutes) or on status change
only. Keep GPS on Kafka.

**Status:** done. Movement ticks do not mark dirty.
`DRIVER_DB_SYNC_SECONDS` default 300. Live first tick wrote 670 last-
seen rows (initial load); the next two ticks wrote 102 and 100 (status
changes). `cdc.drivers` grew **100** messages in 30s (1,832 → 1,932),
not ~600. GPS is `driver_location` (~7k–18k messages after a minute of
ticks).

### 3.2 CDC tables nobody consumes
Was `driver_sessions` and `trip_ratings` on the connector with no
Redis reader.

**Do:** Drop those tables from the connector, or add consumers.

**Status:** done. Live include-list:
`drivers,passengers,trips,city_zones,vehicles`. No `cdc.driver_sessions`
or `cdc.trip_ratings` topics. Slot `hailpath_debezium`. cache-updater
subscribes to `TOPIC_MAP` keys (a `^cdc\\..*` regex missed
`cdc.vehicles` after the first metadata fetch; 0 `vehicle:*` keys,
driver wait stuck). Live Redis `vehicle:*` = 1,060 after that fix.

### 3.3 Sink idempotency is RAM
See 2.3.

**Do:** ReplacingMergeTree on `event_id`. Do not rely on RAM across
recreate.

**Status:** done. Live extra `event_id` rows = 0 on `trip_events` and
`dispatch_offers`.

### 3.4 City writes 256 hotspot rows every 30s
See 2.2.

**Status:** done. Live same as 2.2.

### 3.5 `driver_sessions`: 114,163 rows / 18 MB for 1060 drivers
Was CDC'd, `driver_id` text.

**Do:** Stop CDCing it. Keep `session_id bigint`.

**Status:** done. Not in the CDC include-list. Live `driver_id`
`integer`.

### 3.6 City drain vs 12M `driver_location`
City consumed `driver_location` to score demand. Time-budget drain
(`drain.py`) was added; lag still spiked then fell.

**Do:** Score from a sample or last-per-driver. Do not require the
demand service to ingest the full GPS firehose.

**Status:** done. Live subscribe is `trip_lifecycle` only. Free drivers
come from Redis `geo:drivers:available:*` each score tick. On-trip
speeds come from `hailpath.driver_last_known FINAL` on the traffic
clock. `driver_location` lag on group `hailpath-city` has consumer `-`
(not assigned). Scores still publish (`kafka_rows` 0 / 256 / 2 / 1).

---

## 4. Grafana

### 4.1 Home dashboard scans six hours of events every 10s
Was refresh 10s, 6h `argMax` on `trip_events`, timezone
`America/New_York`.

**Do:** Home: trips/min, lag, open count, map. 15-minute window. 30s
refresh. UTC. Last-status from a small table.

**Status:** done. Live-ops JSON is UTC, 30s, 15m. Open trips read
`trip_last_status` (6,587 rows). Map reads `driver_last_known`.

### 4.2 Forty-six panels, no first question
`panel-probe.py` / `verify-grafana` treat any row as success.

**Do:** One incident home page. Drill-down second. Accept a named
metric, not `rows > 0`.

**Status:** partial. Live-ops is the incident home. Panel-probe still
treats any row as success.

### 4.3 Map is OSM; router is LION
Geomap XYZ is `hailpath-tiles` OSM on `:8080`. Routing uses `ways` /
`ways_vertices_pgr` from LION (live 172,512 ways, 110,057 vertices).

**Do:** LION tiles later. Do not call OSM the product map.

**Status:** open. Later.

---

## 5. Runtime

### 5.1 Debezium RSS 597 MiB / 640m (93%)
Was CDC storm (3.1) plus a tight cgroup.

**Do:** Cut the CDC storm first. Then size heap so RSS stays ~70% of
the cgroup.

**Status:** done. Storm is cut (3.1). Heap stays `-Xmx384m` (Connect
needs it). Laptop cgroup is `DEBEZIUM_MEM=1280m`. Live `docker stats`
**592 MiB / 1.25 GiB (46%)** with the connector RUNNING. 640m was the
working set, not headroom.

### 5.2 Connect `:8083` on `0.0.0.0`, no login
**Do:** Bind `127.0.0.1` or do not publish.

**Status:** done. Live `docker port`: `8083/tcp -> 127.0.0.1:8083`.
`ss` LISTEN `127.0.0.1:8083`. Connector `hailpath-pg` RUNNING.

### 5.3 `make kafka-certs` rewrites the CA on every `make up`
**Do:** Generate only when the files are missing.

**Status:** done. Live `make up` after the wipe regenerated once; the
next `make up` printed `kafka TLS files already present; skipping
generate`.

### 5.4 `z-config/quality.sql` will OOM this host
Was `uniqExact(event_id)` on 12M position UUIDs.

**Do:** `count() - uniq(event_id)` on small tables only.

**Status:** done. `quality.sql` / `profile.sql` do not
`uniqExact` positions. Live uniqueness used `uniq` on `trip_events`
and `dispatch_offers` only.

---

## 6. This repository

### 6.1 Plan documents were a pile
`TODO.md` was a 1900-line journal. `REVIEW.md` was a second list.

**Do:** This file is the list. `TODO.md` stays a pointer. Do not add
another review file.

**Status:** done. This file is the list. `TODO.md` is a pointer.
`REVIEW.md` is absent.

---

## Order

1.1–1.5 keys and types (done, live).
2.1–2.3, 2.6–2.7 positions, hotspots, duplicates, login, grants (done, live).
3.1–3.6 CDC storm, unused CDC, sink, hotspots, sessions, city GPS (done, live).
4.1 home dashboard (done in source).
5.1–5.4 Debezium RSS, Connect bind, cert skip, quality.sql (done, live).
2.4–2.5, 4.2, 4.3 still open.
