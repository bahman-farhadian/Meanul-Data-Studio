# Hailpath issues

Single list of open work. Measured on this laptop 2026-11-20
(`SEED_DRIVERS=1060`). The loop runs. These are why it is not a product
you show yet.

Work top to bottom. One issue per change.

---

## 1. Keys are strings

### 1.1 `trip_id` is `text` / `FixedString(21)`
Live value `trp-20261118-01xleNJo`. 21 bytes. Postgres PK, Kafka key,
ClickHouse `ORDER BY (trip_id, event_time)`. Prefix `trp-` is the column
name. Date is already `event_time`. The random tail exists because the
mint would not use a sequence (`ids.py`).
**Do:** `bigint` / `UInt64` at mint, Avro, Postgres, ClickHouse, Redis.
Format a display string only in logs.

### 1.2 `driver_id` / `passenger_id` are formatted ints
`driver_id(7) -> "drv-0000007"`. 11-byte string of an `int`. 1060
drivers; 106000 still fits `UInt32`.
**Do:** Store `integer` / `UInt32`. Print `drv-…` only when printing.

### 1.3 `zone_id` is text for 1..263
TLC LocationID. `LowCardinality(String)` in CH, `text` in PG.
**Do:** `UInt16` everywhere. Parse TLC text at ingest.

### 1.4 `event_id` UUID on every fact row, and it duplicates
`trip_events`: 29525 rows, 29191 distinct `event_id`. Same UUID appears
three times. Sink was supposed to refuse duplicates. UUID is 16 bytes on
every GPS tick (`driver_positions` is 12.25 million rows, 1 GiB
uncompressed).
**Do:** Fix sink idempotency (it does not hold). Then drop UUID from
position rows; Kafka offset is enough.

---

## 2. ClickHouse

### 2.1 Login opens `default`
`currentUser(), currentDatabase()` → `hailpath`, `default`. Tables are
in `hailpath`. Compose never sets `CLICKHOUSE_DB`.
**Do:** `CLICKHOUSE_DB=hailpath`. Recreate if the user was created
without it. Confirm `currentDatabase() = hailpath`.

### 2.2 User `hailpath` has `GRANT … ON *.*`
FILE, S3, DROP, SYSTEM. Grafana needs SELECT. Sink needs INSERT.
**Do:** Grants only on `hailpath.*`. Disable the image `default` user.

### 2.3 `driver_positions` is the warehouse
12.25 million rows, 462 MiB on disk, 1 GiB uncompressed, on a 2g
cgroup. `uniqExact(event_id)` on that table hit the memory limit and
was killed. 92.8% of rows have `trip_id` NULL and still sit in
`Nullable(FixedString(21))`.
**Do:** Integer `driver_id`, drop `trip_id` from idle ticks (or store it
only when `on_trip`). Sample or downsample idle GPS in the warehouse.
Idle live map reads Redis, not this table.

### 2.4 Rollup counts are `UInt64`
Hourly `completed_trips` max 10. `offers_made` max 29. `UInt32` holds
full-scale 655k/day.
**Do:** `UInt32` when those tables are rebuilt with 1.x.

### 2.5 29 objects, Grafana uses ~14
MVs write on every insert on 2g RAM.
**Do:** Raw facts plus the rollups the home dashboard reads. Nothing
else until a panel needs it.

---

## 3. Grafana

### 3.1 Home dashboard scans six hours of events every 10s
“Open trips by last status” is `argMax` grouped by string `trip_id`
over 6 hours of `trip_events`. Timezone `America/New_York`; the stack
is UTC. Six of nine live-ops panels have no description.
**Do:** Home: trips/min, lag, open count, map. 15-minute window. 30s
refresh. UTC.

### 3.2 46 panels, no first question
Marketplace 14 KPIs. Trip inspector is empty until a variable is set.
`verify-grafana` treats any row as a pass.
**Do:** One incident home page. Drill-down second. Accept a named
metric, not `rows > 0`.

### 3.3 Map is OSM; router is LION
**Do:** LION tiles later. Do not call OSM the product map.

---

## 4. Runtime

### 4.1 Debezium RSS ~94% of 640m
Next snapshot can OOM.
**Do:** Raise cgroup or cut heap so RSS stays near 70%.

### 4.2 Connect `:8083` on `0.0.0.0`, no login
**Do:** Publish `127.0.0.1` only, or do not publish.

### 4.3 `make kafka-certs` rewrites the CA on every `make up`
**Do:** Generate only when the files are missing.

### 4.4 `driver_sessions` is 113k rows / 18 MB for 1060 drivers
Same size as `trips`. Session churn is written like fact data.
**Do:** Confirm what reads this table. If only CDC→Redis, keep a short
TTL or do not CDC it.

---

## 5. This repository

### 5.1 Three “plan” documents
`TODO.md` was a 1900-line journal (cluster names, 2026-10 bring-ups,
Superset). `REVIEW.md` was a second list. `z-docs/schema-review.md` is a
third. Nobody can see what is open.
**Do:** This file is the list. `TODO.md` is a pointer. `REVIEW.md` is
removed. Do not add another.

---

## Order

1.1–1.4 keys (breaking, once).  
2.1–2.2 login and grants.  
2.3 positions table (disk).  
3.1–3.2 Grafana home.  
4.1–4.3 so it does not die or leak.  
The rest when those files are touched. 3.3 LION map last.
