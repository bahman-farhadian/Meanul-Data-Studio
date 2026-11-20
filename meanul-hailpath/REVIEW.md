# Hailpath stack review

The loop runs. The schema is the problem. Ids, types, and Grafana were
built for log lines, not for a warehouse.

---

## Identifiers

This is the main issue. `trip_id` is `trp-20261118-01xleNJo`: a 21-byte
string. `driver_id` is `drv-0001060`: an 11-byte string of a number the
code already had as `int`. Those values are the join key in Postgres,
Kafka, Redis, and ClickHouse (`ORDER BY (trip_id, event_time)`).

### ID-1. `trip_id` is a string

There is no warehouse benefit to a string trip id. ClickHouse and
Postgres compare and group 21 bytes instead of 8. The prefix `trp-` is
the column name. The date `20261118` is already `event_time`. The random
8 characters exist because the mint refused a sequence, then hit
birthday collisions at full-scale seed (documented in `ids.py`).

A `UInt64` (Postgres `bigint`, Avro `long`) is 8 bytes, compares in one
register, and cannot collide if it is a sequence or a snowflake. Display
form `trp-…` belongs in a log formatter, not in the primary key.

**Do:** Mint `bigint` / `UInt64` at the source (`ids.py`, Avro, PG,
ClickHouse). Keep a human form only if a UI needs it, as a generated
label, not as the join key. This is a breaking change across the stack;
do it once, in order: mint → Avro → PG → CH → Redis.

### ID-2. `driver_id` / `passenger_id` are zero-padded strings of ints

`driver_id(number: int) -> "drv-0000001"`. The integer is the identity.
The string is formatting. 11 bytes vs `UInt32` (4 bytes). 1060 drivers
today, 106000 at deploy: both fit in `UInt32`.

**Do:** Store `UInt32` / PG `integer`. Format `drv-…` only when printing.

### ID-3. `zone_id` is text for TLC 1..263

`LowCardinality(String)` on `"1"`..`"263"`. Cardinality is 263. `UInt16`
is 2 bytes and groups without a dictionary.

**Do:** `UInt16` in PG, Avro, CH. Map TLC LocationID at ingest if the
source file is text.

### ID-4. `event_id` is UUID (16 bytes) on every fact row

Used so the sink can skip duplicates. `(topic, partition, offset)` or a
`UInt64` hash of that is 8 bytes and is already unique per Kafka record.

**Do:** Stop carrying UUID on `driver_positions` (the heaviest table).
Idempotency can be Kafka offset, not a 16-byte column on every GPS tick.

---

## ClickHouse

### CH-1. Login opens database `default`

`currentDatabase()` for user `hailpath` is `default`. Tables are in
`hailpath`. Compose never sets `CLICKHOUSE_DB`. DBeaver starts empty.

**Do:** `CLICKHOUSE_DB=hailpath` on the server (and recreate if the user
was created without it). Confirm `currentDatabase() = hailpath`.

### CH-2. Grants are `*.*`

Live grants include FILE, S3, DROP, SYSTEM on every database. Grafana
needs SELECT on `hailpath`. The sink needs INSERT on `hailpath`.

**Do:** `users.d`: only `hailpath.*`. No FILE/S3/URL. Disable the image
`default` user.

### CH-3. Count columns are `UInt64`

Hourly `completed_trips` max on this host is 10. `offers_made` max is 29.
`UInt32` holds 655k trips/day with room. This is smaller than ID-1; do it
when the rollup tables are rebuilt anyway.

**Do:** `UInt32` on SummingMergeTree counts. Recreate those tables.

### CH-4. Too many MVs for the laptop

29 objects in `hailpath`. Grafana queries ~14 names. Every insert fans
out into MVs on a 2g cgroup.

**Do:** Raw facts + the rollups the home dashboard reads. Add MVs when a
panel needs them, not before.

### CH-5. DDL comments are a capacity journal

`002_positions.sql` is mostly 79 GiB / monthly-part history. That is not
the schema.

**Do:** Engine, partition, TTL, one line each. Measurements live in
capacity notes, not in `ddl/`.

---

## Grafana

### GF-1. Home dashboard scans facts every 10s

“Open trips by last status” is `argMax` over 6 hours of `trip_events`
grouped by string `trip_id`, refresh 10s. Timezone is `America/New_York`;
the stack is UTC. Six of nine live-ops panels have no description.

**Do:** Home page: trips/min, lag, open count, map. Window 15 minutes.
Refresh 30s. UTC. If you need last-status, maintain it as a table (or
query `trips` in Postgres), do not fold 6 hours of events on every load.

### GF-2. 46 panels, no first question

Marketplace 14 KPIs, history 10, trip inspector dead until a variable is
set. `verify-grafana` treats “any row” as success.

**Do:** One home dashboard you would open in an incident. Drill-down
pages second. Acceptance: the named metric is present, not `rows > 0`.

### GF-3. Map is OSM; router is LION

Later. Do not treat OSM as the product map.

---

## Runtime (only if it will page you)

### RT-1. Debezium RSS is ~94% of 640m
Heap and cgroup will collide on the next snapshot. Raise mem or cut heap
so RSS sits near 70% of the cgroup.

### RT-2. Connect `:8083` is on `0.0.0.0` with no login
Bind `127.0.0.1` or do not publish it.

### RT-3. `make kafka-certs` rewrites the CA on every `make up`
Generate only when the files are missing.

---

## Order

1. ID-1 … ID-4 (keys). Everything else is cheaper after ids are integers.  
2. CH-1, CH-2 (login and grants).  
3. CH-3, CH-4, CH-5 with the id migration.  
4. GF-1, GF-2.  
5. RT-* when they hurt. GF-3 last.
