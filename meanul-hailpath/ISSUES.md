# Hailpath issues

Single open-work list. Measured 2026-11-20 on this laptop
(`SEED_DRIVERS=1060`, ClickHouse cgroup 2g). The loop runs. This is why
the warehouse and the front end are not ready to show.

Work top to bottom. Schema first, then the services that fill it. One
issue per change.

---

## 1. ClickHouse schema — keys and types as stored

### 1.1 `trip_id` is `FixedString(21)` (Postgres `text`)
Live sample `trp-20261118-01xleNJo`. 21 bytes on every join key.
ClickHouse `ORDER BY (trip_id, event_time)` on `trip_events`,
`dispatch_offers`, `trip_facts`. Avro type is `string`. Mint:
`ids.new_trip_id` → `trp-YYYYMMDD-` + 8 random chars.

There is no warehouse benefit. `trp-` is the column name. The date is
`event_time`. The random tail exists because the mint refused a
sequence, then collided at full-scale seed (`ids.py`). A `UInt64` is 8
bytes and compares in one register.

**Do:** Mint `bigint` / `UInt64` in `ids.py`, Avro `long`, Postgres
`bigint`, ClickHouse `UInt64`, Redis int. Format a display string only
in logs.

### 1.2 `driver_id` / `passenger_id` / `vehicle_id` are formatted ints
Live `drv-0000180`, `psg-0005313`. Stored `FixedString(11)` / PG `text`.
`driver_id(n)` already takes `int` and returns `drv-{n:07d}`.
`vehicles.vehicle_id` is `'veh-' || right(driver_id, 6)`
(`005_vehicles.sql`). 1060 drivers; 106000 still fits `UInt32` (4 bytes
vs 11).

**Do:** Store `integer` / `UInt32`. Print `drv-` / `psg-` / `veh-` only
when printing.

### 1.3 `zone_id` is text for TLC 1..263
Live pickup zones `147`, `26`, `61`. CH `LowCardinality(String)`, PG
`text`, Avro `string`. Cardinality is 263.

**Do:** `UInt16` in PG, Avro, CH. Parse TLC LocationID at ingest.

### 1.4 `event_id` is UUID (16 bytes) on every fact row
Columns on `driver_positions`, `rider_positions`, `trip_events`,
`dispatch_offers`, `hotspot_history`, `segment_traffic_history`,
`trip_facts`. Avro carries it as `string` (not uuid logical type)
because `json.dumps` cannot write a UUID object.

On `driver_positions` that is 16 bytes × 12.39 million rows. Kafka
`(partition, offset)` is already unique per message.

**Do:** Stop storing UUID on position rows. Keep a cheaper idempotency
key (offset, or `UInt64`) if the sink still needs one.

### 1.5 Rollup counts are `UInt64` for values of 10–29
`trip_stats_hourly.completed_trips` max = 10.
`dispatch_funnel_hourly.offers_made` max = 29. Same `UInt64` on
fulfilment, utilization ticks, daily completed. Full-scale 655k
trips/day still fits `UInt32`.

**Do:** `UInt32` when those tables are rebuilt with 1.1–1.3.

---

## 2. ClickHouse tables, size, correctness

### 2.1 `driver_positions` is the warehouse
Live `system.parts`: **12,388,084 rows**, 467 MiB on disk, **1.01 GiB
uncompressed**, on a 2g cgroup. `countIf(trip_id IS NULL)` = 11,494,581
(**92.8%**). Those rows still occupy `Nullable(FixedString(21))`.
`uniqExact(event_id)` on this table was killed at 1.20 GiB
(`MEMORY_LIMIT_EXCEEDED`). Kafka `driver_location` high-watermark
**12,304,017** messages. TTL is 2 days; the table is already this size
in hours.

The live map reads Redis, not this table (`002_positions.sql` says so).

**Do:** Integer `driver_id`. Do not store `trip_id` on idle ticks. Do not
land every GPS tick in MergeTree; sample, or keep only `on_trip` /
last-known in the warehouse. Idle stream stays on Kafka/Redis.

### 2.2 `hotspot_history` is a 30-second snapshot dump
570,624 rows, 9.5 MiB disk. Kafka `city_hotspots` **564,992** messages.
City publishes **every zone every 30s** (`city_service/__main__.py`).
256 zones × 2/min ≈ 30k rows/hour. TTL 30 days.

**Do:** Write a row when the score changes, or at 5 minutes. Redis
already holds the current value (`hotspot:{zone}:{period}`, 6h TTL).

### 2.3 Duplicate `event_id` on `trip_events`
29,803 rows, `uniq(event_id)` = 29,466. **332** ids appear more than
once (**669** extra rows). `z-config/quality.sql` counts
`count() - uniqExact(event_id)` as a FAIL bar — and that same file also
runs `uniqExact(event_id)` on `driver_positions`, which OOMs this host.

Sink (`seen.py`) keeps ids **in process memory**, empty on restart.
Consumer uses `auto.offset.reset=earliest` (`from_beginning=True`).
Crash or recreate between insert and `commit()` replays the batch;
SeenEvents is empty; ClickHouse has no UNIQUE.

**Do:** Idempotent insert in ClickHouse (ReplacingMergeTree on
`event_id`, or `INSERT` with a keep-last table), or persist seen ids.
Stop using `uniqExact` on `driver_positions` in `quality.sql`.

### 2.4 Twenty-nine objects; Grafana uses about fourteen
29 tables/MVs in `hailpath`. Dashboard JSON references ~14 names.
Every insert into `trip_events` / `driver_positions` fans into MVs on
2g RAM (`trip_stats_*`, `fulfilment_*`, `driver_activity_*`,
`driver_utilization_*`, `od_matrix_*`, `trip_duration_percentiles_*`,
`active_entities_*`).

**Do:** Raw facts + the rollups the home dashboard reads. Add MVs when a
panel needs them.

### 2.5 DDL files are a capacity journal
`002_positions.sql` is mostly 79 GiB / monthly-part history for a 106k
fleet. This host is `CH_MEM=2g`.

**Do:** Engine, partition, TTL. One line each. Measurements out of
`ddl/`.

### 2.6 Login opens database `default`
`SELECT currentUser(), currentDatabase()` → `hailpath`, **`default`**.
Tables are in `hailpath`. Compose never sets `CLICKHOUSE_DB` (the
sample-stack does). Grafana `defaultDatabase: hailpath` hides this;
`clickhouse-client` and DBeaver do not.

**Do:** `CLICKHOUSE_DB=hailpath` on the server. Recreate if the user was
created without it. Confirm `currentDatabase() = hailpath`.

### 2.7 User `hailpath` has `GRANT … ON *.*`
Live grants: FILE, S3, URL, DROP, SYSTEM, CLUSTER, `MOVE PARTITION
BETWEEN SHARDS` on every database. Grafana needs SELECT. Sink needs
INSERT. `ch-ddl-init` needs CREATE.

**Do:** `users.d`: only `hailpath.*`. Disable the image `default` user
(the entrypoint still writes `users.d/default-user.xml`).

---

## 3. Services (they produce this schema)

### 3.1 Driver writes GPS into Postgres every tick → CDC storm
`sync_to_database` `UPDATE drivers SET last_lat, last_lon, status …`
for every dirty driver (`driver_service/__main__.py`). Tick log:
`database_rows_updated` ~600. Debezium tails `hailpath.drivers`.

Kafka **`cdc.drivers` 1,343,555 messages** for **1060** driver rows.
The profile is 696 kB in Postgres. The change stream is the GPS stream
copied into WAL.

Positions belong on `driver_location`, not in `drivers` at tick rate.

**Do:** Persist last-seen on a long timer (minutes) or on status change
only. Keep GPS on Kafka. That also cuts WAL and `cdc.drivers`.

### 3.2 CDC tables nobody consumes
Connector `table.include.list` has `driver_sessions` and `trip_ratings`.
`cache_updater.TOPIC_MAP` has drivers, vehicles, passengers, trips,
city_zones only.

Kafka: `cdc.driver_sessions` **227,986** messages;
`cdc.trip_ratings` **33,600**. Nothing applies them to Redis. WAL still
moves.

**Do:** Drop those tables from the connector, or add consumers. Do not
CDC a table with no reader.

### 3.3 Sink idempotency is RAM
See 2.3. `SeenEvents` default 500_000 ids, process-local.
`from_beginning=True` means a new consumer group replays the whole
log into ClickHouse.

**Do:** Commit offsets and/or use a CH engine that collapses on
`event_id`. Do not rely on RAM across `docker compose up --force-recreate`.

### 3.4 City writes 256 hotspot rows every 30s
See 2.2. Redis already has the current score. The warehouse does not
need a row per zone per tick.

**Do:** Publish Kafka/CH on change, or every 5 minutes (same clock as
traffic). Keep Redis at 30s if drivers need it.

### 3.5 `driver_sessions`: 114,163 rows / 18 MB for 1060 drivers
Same disk as `trips` (18 MB). PK is `bigint` (good). `driver_id` is
still `text`. CDC'd, not cached (3.2). ClickHouse utilization is
sourced from `driver_positions`, not this table (`008_driver_utilization.sql`).

**Do:** If nothing reads it except WAL, stop writing it every shift
flicker, or stop CDCing it. Keep `session_id bigint`.

### 3.6 City drain vs 12M `driver_location`
City consumes `driver_location` (12 partitions, 12M messages) to score
demand. A 5000-message cap fell behind (~6700 positions/tick). Time-
budget drain (`drain.py`) was added; lag still spikes then falls.

**Do:** Score from a sample or last-per-driver. Do not require the
demand service to ingest the full GPS firehose.

---

## 4. Grafana

### 4.1 Home dashboard scans six hours of events every 10s
`hailpath-live-ops.json`: refresh 10s. Panel “Open trips by last
status” is `argMax(status, event_time)` grouped by string `trip_id`
over 6 hours of `trip_events`. Timezone `America/New_York`; the stack
is UTC. Six of nine live-ops panels have empty `description`.
Datasource `defaultDatabase` is `hailpath` (correct); the server user
is not (2.6).

**Do:** Home: trips/min, lag, open count, map. 15-minute window. 30s
refresh. UTC. Last-status from a small table, not a 6h fold.

### 4.2 Forty-six panels, no first question
Marketplace 14 KPIs. History 10. Trip inspector is empty until a
template variable is set. `panel-probe.py` / `verify-grafana` treat
any row as success.

**Do:** One incident home page. Drill-down second. Accept a named
metric, not `rows > 0`.

### 4.3 Map is OSM; router is LION
Geomap XYZ is `hailpath-tiles` OSM on `:8080`. Routing uses
`ways` / `ways_vertices_pgr` from LION.

**Do:** LION tiles later. Do not call OSM the product map.

---

## 5. Runtime

### 5.1 Debezium RSS 597 MiB / 640m (93%)
Live `docker stats`. Next snapshot or `cdc.drivers` burst can OOM.
CDC volume is 3.1 (GPS into `drivers`).

**Do:** Cut the CDC storm first. Then size heap so RSS stays ~70% of
the cgroup.

### 5.2 Connect `:8083` on `0.0.0.0`, no login
`d-infra-debezium/docker-compose.yaml` publishes
`${PROXY_DEBEZIUM_PORT:-8083}:8083`. REST PUT can register a connector.

**Do:** Bind `127.0.0.1` or do not publish. Traffic is on `hailpath-net`.

### 5.3 `make kafka-certs` rewrites the CA on every `make up`
`c-infra-kafka/security/generate.sh`: “Regenerates certs each time”.
`Makefile` `up` always calls `kafka-certs`. Running broker keeps the
old cert in memory.

**Do:** Generate only when the files are missing.

### 5.4 `z-config/quality.sql` will OOM this host
It runs `uniqExact(event_id)` on `driver_positions` (12M UUIDs).
Already killed the 2g server once. `make verify-quality` is unsafe
until 2.1 / 2.3 are fixed.

**Do:** `count() - uniq(event_id)` on small tables only, or sample.

---

## 6. This repository

### 6.1 Plan documents were a pile
`TODO.md` was a 1900-line journal (old cluster names, Superset,
2026-10 bring-ups). `REVIEW.md` was a second list. `z-docs/schema-review.md`
is a third (now marked archive).

**Do:** This file is the list. `TODO.md` stays a pointer. Do not add
another review file. `clickhouse-design.md` and `z-docs/DATA_DICTIONARY.md`
are reference, not the backlog.

---

## Order

1.1–1.5 ClickHouse/PG/Avro keys and types (breaking, once).  
2.1–2.3 positions, hotspots, duplicate events (disk and truth).  
2.6–2.7 login and grants.  
3.1–3.3 driver CDC storm, unused CDC, sink RAM.  
4.1–4.2 Grafana home.  
5.1–5.4 so it does not die.  
2.4–2.5, 3.4–3.6, 4.3 when those files are touched.
