# Meanul Data Studio — Assessment Standard

This file is the assessment contract for **every version** in this
repository. It is the high-level design rule for how a version may store
data, move data, generate data, and simulate activity. Later versions
inherit §§1–8 unchanged. A version adds a numbered profile at the end; it
does not replace this file.

A version is a sibling directory under the repo root. Today that is
`not-uber-service/` (NYC ride-hail). Future versions (streaming, flights,
finance, …) sit next to it and bind the same studio bars to their own
schema, topics, generators, and Makefile.

A bar that cannot be scored is not a bar. Grafana “looks correct” is not a
bar. A ratio without a threshold is not a bar.

Numeric thresholds and closed-set symbols in a version profile are copied
from that version’s shipped sources (SQL, Avro, ClickHouse DDL, Python).
If this file and those sources disagree, the sources win and this file is
wrong. `not-uber-service/z-lib/nus-common/tests/test_assessment_standard.py`
fails when the studio rules or the version-1 numbers drift from those
sources.

---

## 1. How a bar is scored

Every bar has all four of:

1. **Name** — what is being measured.
2. **Instrument** — an existing Makefile target, SQL file, pytest path, log
   field, HTTP GET, or a shipped source file. Studio-wide bars name the
   *kind* of instrument. A version profile binds that kind to a concrete
   command.
3. **Pass** — a numeric value, a required string, a required count, a
   closed set of symbols, or “exit 0”.
4. **Fail** — the complementary value. Silence, a screenshot, or a ratio
   without a threshold is not a fail line.

Commands named as `make …` in §9 run from `not-uber-service/` unless
noted. Other versions name their equivalents in their profile.

---

## 2. Not bars

These exist and may be read for diagnosis. They do **not** pass or fail a
version:

| Signal | Why it is not a bar |
| --- | --- |
| A dashboard “looks right” | Visual only. Tiles, if the domain has a map, are scored by HTTP. Simulation is scored by SQL, Avro, warehouse rows, and generator logs. |
| A heading/axis ratio with no threshold | Geometry relative to true north is not proof of following a network. Version 1’s `axis_share` / `both_axes_pct` are this class. |
| Seed knobs themselves (`SEED_*`, history days, request rate) | Config. The bar is that the stores match whatever those knobs are set to. |
| Empty last-window immediately after bring-up | Generators have not finished a cycle. Zero rows in a one-hour window is “not yet”, not a pass. |
| Cache empty in a domain that only live coordinators write | History is not required there. Version 1 Redis db 3 (live trips) is this class at `up`. |
| OLTP row count << seeded history after the archiver’s first ticks | Seeded history is older than retention. The warehouse keeps it. |
| HAProxy / Patroni role noise on a node that is still in one of the two pools | A node down in **both** write and read is the failure. |

---

## 3. Roles every version implements

A version is a set of Docker components that map onto these roles. The
alphabetic directory prefixes (`a-infra-…`, `j-service-…`) are a version’s
build order, not the studio vocabulary. High-level design is written in
roles. A version profile states the directory that fills each role.

| Role | Responsibility |
| --- | --- |
| `oltp` | PostgreSQL cluster. Operational source of truth. Generators and coordinators write durable entity state here. |
| `cache` | Sentinel-managed Redis. The read path for every service that is not the OLTP owner. |
| `broker` | Kafka in KRaft mode, Schema Registry, binary Avro. Cross-process events. |
| `cdc` | Debezium. PostgreSQL WAL → `cdc.*` topics. The only path from OLTP to cache. |
| `warehouse` | ClickHouse cluster. Analytics. Grafana and Superset query this, nothing else. |
| `live-ui` | Grafana. Operational views on warehouse Distributed tables. |
| `analytic-ui` | Superset. Analytical BI on the same warehouse. Empty panels before data are fine; an HTTP error is not. |
| `bootstrap` | One-shot: migrations, reference data, history, warehouse backfill, bootstrap-done marker. Then it exits. |
| `cache-updater` | Applies `cdc.*` to Redis. Idempotent last-write-wins. Does **not** wait for bootstrap. |
| `generator` | Long-running Faker service. One process, many simulated entities. Writes profiles/state to OLTP and high-frequency events to the broker. |
| `coordinator` | Matches, assigns, prices, or scores using cache + broker. May write OLTP. Must not become the read path for other services. |
| `warehouse-sink` | Broker → cache enrich → warehouse Distributed tables. Lookups from Redis, never from OLTP. |
| `archiver` | Deletes aged operational rows from OLTP. Analytics remain in the warehouse. |
| `stack-config` | Load balancer, assessment SQL, stack-wide templates. |
| `shared-lib` | Id minting, clients, clocks, geometry/helpers. One mint, one key name, one client. |

A version may have several `generator` and `coordinator` directories. It
may omit a geographic tile sidecar only when the domain has no map; the
profile must say so. It may not omit `oltp`, `cache`, `broker`, `cdc`,
`warehouse`, `bootstrap`, `cache-updater`, `warehouse-sink`, or
`shared-lib`.

---

## 4. Schema contract

Three copies of the same operational fact exist in a running version:
the OLTP row, the Avro record on the broker, and the warehouse row. They
are one schema in three physical forms. A closed set of symbols, an id
width, and a timestamp policy that disagree across those forms is a fail
even if each store is internally consistent.

### 4.1 OLTP (`oltp` + `bootstrap` migrations)

| # | Bar | Pass | Fail | Instrument kind |
| --- | --- | --- | --- | --- |
| S1 | Source of truth | After bootstrap, core entity tables exist and seeded counts equal the version’s knobs | Empty core tables, or counts that ignore the knobs | Version `verify-data` |
| S2 | Migrations | Plain ordered SQL (`001_*.sql`, `002_*.sql`, …), applied once by bootstrap | A second migration framework, or unordered ad-hoc DDL from generators | `bootstrap` migrations directory |
| S3 | Documents | Semi-structured payloads are PostgreSQL `jsonb`. No document database in compose | MongoDB (or equivalent) as a store for JSON-like payloads | Migrations; version compose |
| S4 | Closed sets | Status / type / period columns have a `CHECK (… IN (…))`. The symbol list is the Avro enum and the warehouse `Enum8` | A symbol accepted in one store and rejected in another | Pytest reads migrations, `.avsc`, warehouse DDL |
| S5 | Minted ids | Entity ids this codebase creates are minted in `shared-lib` only, with a declared width | Ad-hoc `uuid4()` / mixed prefixes in a generator | `shared-lib` id module; warehouse `FixedString(N)` |
| S6 | External ids | Ids minted outside the codebase (city codes, IATA, catalog ids) are `text` / `String` / `LowCardinality(String)`, not `FixedString` | Forcing an external variable-width id into `FixedString` | Warehouse DDL vs the mint module |
| S7 | Foreign keys | Generated references (`REFERENCES`) exist. Bootstrap and live inserts satisfy them | Orphan `rider_id` / equivalent | Migrations; version `verify-data` |
| S8 | Time | Columns are `timestamptz`. Stored values are UTC (`TZ=UTC`). Demand / local calendars use `SIM_TIMEZONE` | Naive local time stored; slim image missing `tzdata` so `ZoneInfo` is silently UTC | Compose `TZ` / `SIM_TIMEZONE`; `shared-lib` clock helper |
| S9 | No tick table | High-frequency playhead / position / progress is **not** an OLTP tick table. OLTP may hold a last-known point on the entity row | `INSERT` per tick into Postgres | Migrations (absence of the tick table); warehouse table for the stream |
| S10 | CDC-unsafe columns | `geometry`, `bytea`, and other types Debezium cannot serialize are listed in the connector `column.exclude.list` | Geometry on a `cdc.*` topic (`DataException`) | CDC connector JSON |

### 4.2 Warehouse (`warehouse`)

| # | Bar | Pass | Fail | Instrument kind |
| --- | --- | --- | --- | --- |
| S11 | Client names | Services and dashboards query Distributed table names, never `*_local`, never Kafka, never Redis, never OLTP | Grafana SQL on `*_local` or on a `cdc.*` topic | Version dashboard check |
| S12 | Minted id type | Warehouse columns for minted ids are `FixedString(N)` where `N` is the mint width in `shared-lib` | `FixedString` reject at insert; or `String` for a mint this codebase controls | Warehouse DDL; `shared-lib` |
| S13 | Closed-set type | Warehouse columns for S4 sets are `Enum8` with the same symbols, same spelling | A new category silently accepted as a string | Warehouse DDL vs Avro vs `CHECK` |
| S14 | Event time | Event timestamps are `DateTime64(3, 'UTC')` (or equivalent UTC DateTime64) | Timezone-less DateTime, or a non-UTC timezone | Warehouse DDL |
| S15 | Cluster | Declared shard × replica members present; `absolute_delay` at or near 0 | Member missing; delay only growing | Version `verify-ch` |
| S16 | TTL | High-volume playhead tables declare a TTL. Business-event tables keep a longer TTL than playhead | Unbounded playhead growth | Warehouse DDL |

### 4.3 Alignment

| # | Bar | Pass | Fail | Instrument kind |
| --- | --- | --- | --- | --- |
| S17 | Three-copy alignment | For every closed set that exists in more than one store, the symbol lists are equal. For every minted id, the width is equal | Drift between migration, `.avsc`, and DDL | Pytest in `shared-lib` / version tests |
| S18 | Field names | Avro field names that the sink writes are the warehouse column names, or the sink has an explicit rename. Silent mismatch is a fail | Sink insert errors / NULLs where the producer sent a value | `.avsc` vs warehouse DDL vs sink |

---

## 5. Message-broker contract

Cross-process events travel on Kafka as binary Avro. The Schema Registry
is the schema. JSON on a declared topic, or a consumer that joins OLTP to
interpret a message, is a fail.

| # | Bar | Pass | Fail | Instrument kind |
| --- | --- | --- | --- | --- |
| B1 | Encoding | Every declared (non-CDC) topic has a `.avsc` in the broker piece. Producers use the shared Avro producer (`acks=all`, idempotent) | JSON/plaintext on a declared topic; fire-and-forget producer | Broker `schemas/` vs `topics.tsv`; `shared-lib` Kafka client |
| B2 | Topic inventory | Declared topics live in one TSV (or equivalent) the create-topics one-shot reads. `cdc.*` are **not** in that list — Debezium creates them | Hand-created `cdc.*` in the TSV; a producer topic with no `.avsc` | `topics.tsv`; CDC connector |
| B3 | Durability | Declared topics replication factor 3. Live ISR = 3 on a healthy cluster. Write quorum `min.insync.replicas=2` | ISR < 3 on a declared topic while all brokers are up | Version `verify-kafka`; create-topics script |
| B4 | Entity keys | The TSV `key` column is the entity id. All messages about one entity keep order on one partition | Unkeyed high-volume stream; key that is not the entity | `topics.tsv` |
| B5 | CDC | Connector and task `RUNNING`. Replication slot active, `wal_status` `reserved`. One compact `cdc.<table>` topic per included table | Slot missing or inactive (PostgreSQL keeps WAL forever). Connector RUNNING with task FAILED | Version `verify-cdc` |
| B6 | No OLTP reads on the consume path | Consumers of broker events look up enrichment in Redis. They do not `SELECT` OLTP for joins | Sink or coordinator querying Postgres per event | Sink / coordinator source |
| B7 | Cache before announce | If a consumer of an event needs cache state the producer owns, that write happens **before** the produce | Event arrives, cache key empty, generator skips the real path | Coordinator source order; live simulation bars |
| B8 | `cdc.*` vs declared | `cdc.*` carry OLTP row images for the cache. Declared topics carry domain events for coordinators and the warehouse. A generator does not publish a domain event by writing OLTP and hoping CDC fans it out to the warehouse | Warehouse fed only from CDC row images for a high-frequency stream | Connector `table.include.list`; warehouse-sink subscriptions |

Patroni physical slots (`*_pg_*`) are replica slots. They are not bar B5.

---

## 6. Cache contract

| # | Bar | Pass | Fail | Instrument kind |
| --- | --- | --- | --- | --- |
| C1 | Read path | Services that are not the OLTP owner read Redis, not PostgreSQL | `PG_HOST` used for a lookup/join in a generator, sink, or dashboard | Source; compose |
| C2 | Fill path | PostgreSQL changes reach Redis only via Debezium CDC over Kafka | A generator writing Redis copies of rows it also wrote to OLTP (split brain) | `cache-updater`; generators |
| C3 | Logical DBs | Entity domains occupy declared Redis logical databases in `shared-lib`. Diagnosis is `DBSIZE` per db, not a scan of db 0 | Ad-hoc db numbers in a service | `shared-lib` Redis client |
| C4 | Idempotent apply | `cache-updater` upserts are last-write-wins per key. Replay is safe | Read-modify-write across two CDC streams into one key | `cache-updater` |
| C5 | Bootstrap exception | `cache-updater` does not wait for the bootstrap marker. Every `generator`, `coordinator`, `warehouse-sink`, and `archiver` does | Updater waits (deadlock: it is what fills the cache). A generator producing against an empty schema | Source: `wait_for_bootstrap` present or absent |
| C6 | Domain fill | After the CDC snapshot has drained, the Redis db for each seeded entity domain is non-zero | Seeded-domain db still 0 after CDC | Version `verify-data` Redis dbsize; `lag` on the updater group |

---

## 7. Data-generation contract

Generation is a studio feature, not a version trick. Faker produces
internally consistent records that satisfy the schema in §4. Pacing is
config. Services simulate *pools*, not one container per person.

| # | Bar | Pass | Fail | Instrument kind |
| --- | --- | --- | --- | --- |
| G1 | Marker first | Generators call `wait_for_bootstrap` (or the version’s equivalent) before producing. The marker is a Redis key set as bootstrap’s last act | Generating against unmigrated / unseeded OLTP | `shared-lib` lifecycle; version `verify-data` marker |
| G2 | Cache then produce | A generator that reads profiles from Redis waits for at least one key in its domain after bootstrap | Process exits or produces while its cache db is empty | Generator source `wait_for`; version `lag` |
| G3 | Seed equals knobs | After bootstrap, OLTP counts for seeded entities equal `SEED_*` (or the version’s names). Reference datasets the version ships (zones, catalog, graph) are non-zero | Count 0 with knobs > 0. Graph/catalog 0 means the import did not run | Version `verify-data` |
| G4 | History then live | Warehouse business-event count after bootstrap is at least the version’s history product (days × per-day). Live traffic then appends | 0 warehouse events after a finished `up` | Version `verify-data` |
| G5 | One process, many entities | One container per generator role, internally multiplexing the pool | One container per simulated person | Compose service list |
| G6 | Independent generators | Two sides of a marketplace (or producer/consumer pair) are separate processes that happen to share ids, not one loop writing both streams | A single loop duplicating one simulation onto two topics | Generator source |
| G7 | Schema-valid Faker | Inserts satisfy `CHECK`, foreign keys, and minted id shape. A write error on warehouse `FixedString` / `Enum8` is a generation bug | Mixed prefixes; status string outside S4 | OLTP errors; warehouse insert errors; unit tests |
| G8 | Pacing is config | Volume, rate, and time-of-day weights come from `.env` / settings, not literals in the service | Hard-coded fleet size or request rate in a generator | Settings modules; `.env.example` |
| G9 | Internally consistent | Where the version has a real reference surface (street graph, catalog, schedule), Faker draws from it. Random points that ignore that surface are a fail of §8, not a style choice | Lat/lon uniform in a bounding box that includes water / off-catalog ids | Version simulation SQL; generator source |
| G10 | Secrets | `.env` is untracked. No passwords, tokens, or connection strings in git | `.env` committed; a secret in a fixture | `.gitignore`; source |

### 7.1 Data-quality bars (version 1: `make verify-quality`)

G1–G10 above prove generation ran and produced rows of the right shape.
These prove the rows can be *trusted*. Every one is **structural** — an
invariant that must hold whatever the seed knobs say. That is the whole
distinction: a bar whose value moves with `SEED_DRIVERS` measures the
seed settings, not the pipeline, and belongs in `make profile`, which is
for reading rather than for passing.

| # | Bar | Pass | Fail |
| --- | --- | --- | --- |
| Q1 | Every event id is unique | `count() = uniqExact(event_id)` on all five event tables | Any duplicate. ClickHouse does not deduplicate, so every aggregate below is void |
| Q2 | No blank event id | Zero all-zero UUIDs | Any. A row the sink cannot dedupe on |
| Q3 | No negative milestone lag | Zero negative `match_s`/`accept_s`/`arrive_s`/`wait_s`/`ride_s` | Any. A trip whose clock runs backwards |
| Q4 | Arrival follows acceptance | Zero trips arrived-without-accepted, or started-without-arrived | Any |
| Q5 | No-show only after arrival | Zero `rider_no_show`/`wait_too_long` with no `arrived_at` | Any. A driver cannot report a no-show without being there |
| Q6 | One acceptance per trip | Zero trips with more than one accepted offer | Any. Two cars sent to one rider |
| Q7 | Offer chains have no gaps | `count() = max(sequence)` per trip | Any. Dispatch lost track of its own search |
| Q8 | Unmatched trips took no offer | Zero `no_driver_found` trips with an accepted offer | Any |
| Q9 | Party fits the tier's seats | Zero trips with `passenger_count > 4` outside `xl` | Any |
| Q10 | No fare without completion | Zero non-completed trips carrying `fare_final` | Any. Revenue that was never earned |
| Q11 | No completion without fare | Zero completed trips missing `fare_final` | Any |
| Q12 | Payout never exceeds fare | Zero trips where `driver_payout > fare_final` | Any |
| Q13 | Every ended trip has a fact | `uniqExact(trip_id)` over terminal events equals `trip_facts` rows | Any gap. The materialized view is not firing |
| Q14 | No offer for an unknown trip | Zero `dispatch_offers` trip ids absent from `trip_events` | Any |

### 7.2 Capacity (version 1: `make capacity`)

Bytes per row is the number that transfers between scales; a current
total does not. The projection multiplies it by full-scale
`SEED_DRIVERS` over the value actually in use, halves it for the two
shards, and must leave **30% headroom** against the per-node quota — a
warehouse planned to exactly fill its disk cannot merge, because a merge
needs room for the new part before it can drop the old ones.

**Partition granularity follows the TTL, not a house style.** A table
expiring in three days partitions by day, so the expiry drops a
directory; under a monthly partition it would rewrite the whole month
minus the expired rows, every time, on the heaviest table in the stack.
A table keeping 365 days partitions by month, because daily would give
it 365 parts for nothing. `test_partition_granularity_follows_the_ttl`
holds both halves.

`make up` does not rebuild images. A code change is `make build` then
bring-up. Downloads (maps, catalogs) are not required on every destroy.
That is an operations rule, not a data-quality bar; the version Makefile
help must say it.

---

## 8. Simulation-accuracy contract

Simulation is the generators and coordinators walking the schema and the
broker in the real order, on the real constraint surface. The warehouse
must see that walk. A dashboard is not the proof.

| # | Bar | Pass | Fail | Instrument kind |
| --- | --- | --- | --- | --- |
| M1 | Constraint surface | Simulated motion or choice stays on the domain’s network (streets, catalog, schedule, …). The version profile names the surface and a membership/distance test with a numeric fail line | Euclidean hop; off-network point; off-catalog id | Version SQL / pytest named in the profile |
| M2 | State machine | Lifecycle symbols are walked in the declared order. After live traffic, warehouse rows per entity > 1 (the walk, not only the terminal) | Live stack with `rows_per_entity` = 1 forever | Warehouse profile query |
| M3 | Side-effect order | Durable OLTP write and the cache write a consumer needs both happen before the broker announce (B7) | Announce first → empty cache → generator falls off the constraint surface | Coordinator source; M1 live |
| M4 | Empty window is not a pass | A last-window count of 0 means wait and re-run. Off-network = 0 with trips = 0 is not a pass | Treating a silent window as green | Version profile |
| M5 | Playhead on the broker | High-frequency telemetry is Avro on a declared topic → warehouse. OLTP last-known is a timer, not the stream | Per-tick OLTP inserts | S9; warehouse freshness |
| M6 | Dashboards are not the score | Live-ui / analytic-ui health is process + HTTP + “queries Distributed tables”. Generation quality is G* and M* | Screenshot of Grafana | Version `grafana-health` / dashboard check |

A version that has a map additionally scores same-origin tiles (HTTP 200
`image/png` through the load balancer, no third-party tile CDN). A version
that does not have a map says so in its profile and skips that bar.

---

## 9. Version 1 — `not-uber-service` (NYC ride-hail)

This profile binds §§3–8 to shipped files and Makefile targets in
`not-uber-service/`. Commands run from that directory.

### 9.1 Role mapping

| Role | Directory |
| --- | --- |
| `oltp` | `a-infra-postgres` |
| `cache` | `b-infra-redis` |
| `broker` | `c-infra-kafka` |
| `cdc` | `d-infra-debezium` |
| `warehouse` | `e-infra-clickhouse` |
| `live-ui` | `f-infra-grafana` (Grafana **and** `nus-tiles`) |
| `analytic-ui` | `g-infra-superset` |
| `bootstrap` | `h-bootstrap` |
| `cache-updater` | `i-service-cache-updater` |
| `generator` | `j-service-driver`, `k-service-passenger` |
| `coordinator` | `l-service-dispatch`, `m-service-city` |
| `warehouse-sink` | `n-service-clickhouse-sink` |
| `archiver` | `o-service-archiver` |
| `stack-config` | `z-config` |
| `shared-lib` | `z-lib` (`nus-common`) |

### 9.2 Studio bar → v1 instrument

| Studio | v1 binding |
| --- | --- |
| S1, G3, G4, C6 | `make verify-data` |
| S4, S12, S13, S17, G7 | `make verify-walk`; `test_assessment_standard.py` |
| S5 | `z-lib/nus-common/nus_common/ids.py` (`drv-` 11, `psg-` 11, `trp-` 21) |
| S8 | Compose `TZ=UTC`, `SIM_TIMEZONE`; `nus_common/geo.py` |
| S9, M5 | No `driver_positions` table in `h-bootstrap/migrations/`; ticks on `driver_location` → `nus.driver_positions` |
| S10 | `d-infra-debezium/connectors/nus-pg.json` `column.exclude.list` includes `nus.trips.route` |
| S11 | `make grafana-check` (uid `nus-clickhouse`, Distributed names, no `maptiler`) |
| S15 | `make verify-ch` (four members, `absolute_delay` ~ 0) |
| B1, B2, B4 | `c-infra-kafka/topics/topics.tsv` + `c-infra-kafka/schemas/*.avsc` |
| B3 | `make verify-kafka`; `create-topics.sh` `--replication-factor 3` `min.insync.replicas=2` |
| B5 | `make verify-cdc` (connector `nus-pg`, slot `nus_debezium`, plugin `pgoutput`, `wal_status` `reserved`) |
| B7, M3 | `l-service-dispatch/dispatch_service/__main__.py`: `store_live_state` then `announce` |
| C3 | `nus_common/redis_client.py` DB 0–4 |
| C5 | `wait_for_bootstrap` in every generator/coordinator/sink/archiver; **absent** from `i-service-cache-updater` |
| G1 | Redis `system:bootstrap:done` = 1 via `make verify-data` |
| G5, G6 | Compose: one `driver-service`, one `passenger-service` |
| M1 | `make verify-positions` running `z-config/check-on-network.sql` |
| M2 | `make profile` section 2 `rows_per_trip` > 1 after live traffic |
| M4 | Last-hour `trips` = 0 → wait; do not pass §9.4 on an empty window |
| M6 + tiles | `make grafana-health`; `make tiles-health`; `make grafana-check` |

Version-1 not-bars (in addition to §2): `axis_share` / `both_axes_pct` in
`z-config/check-positions.sql` and `z-config/profile.sql` section 10b
(Manhattan streets are rotated ~29° from true north; ~0.71 is expected on
a real polyline). `make profile` section 1 (warehouse freshness) **is** a
bar (S15 / n-service-clickhouse-sink).

### 9.3 Component clauses

#### `a-infra-postgres`

Patroni PostgreSQL (one Leader, two Replicas) and etcd.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Cluster shape | Exactly one Leader, two Replicas, lag at or near 0 MB | Two leaders, zero leaders, or replica lag growing | `make verify-pg` |
| Write path | Clients use HAProxy `5432` (write) / `5433` (read), never a `pg-*` hostname | Direct `pg-*` in a service’s `PG_HOST` | compose `PG_HOST` is `nus-lb-a` / `lb-a` |
| etcd after first start | Local `ETCD_INITIAL_CLUSTER_STATE=existing`; file not committed | `existing` committed to git | `a-infra-postgres/etcd.env` stays `new` in git; `make etcd-existing` is local |

#### `b-infra-redis`

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

#### `c-infra-kafka`

KRaft brokers, Schema Registry, Avro under `c-infra-kafka/schemas/`, topics in
`c-infra-kafka/topics/topics.tsv`. `cdc.*` are created by Debezium, not that
TSV.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Brokers and RF | Three brokers; declared topics RF 3, ISR 3 | ISR < 3 on a declared topic | `make verify-kafka` |
| ksqlDB | Server status RUNNING | Not RUNNING | `make verify-ksqldb` |

Version-1 declared topics: `driver_location`, `rider_location`,
`trip_requests`, `trip_lifecycle`, `dispatch_offers`, `city_hotspots`,
`segment_traffic_updates`.

Every record on every one of them carries the same envelope — `event_id`,
`event_version`, `producer`, `correlation_id` — stamped by the producer,
never by a caller. `event_id` is load-bearing rather than decorative:
ClickHouse does not deduplicate on its own (`ReplacingMergeTree` collapses
only eventually, only within a partition, and only on merge), so the sink
refuses an `event_id` it has already written and that is what makes every
count downstream trustworthy.

Money crosses the wire as Avro `decimal(10,2)`, matching `numeric(10,2)`
in Postgres and `Decimal64(2)` in ClickHouse — one representation end to
end, with no `double` in the middle. fastavro rejects a float for such a
field, so this cannot be got wrong quietly.

#### `d-infra-debezium`

Connector `nus-pg`, slot `nus_debezium`, plugin `pgoutput`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Connector | `"state":"RUNNING"` on connector **and** task | Connector RUNNING with task FAILED | `make verify-cdc` |
| Slot | `nus_debezium` active `t`, `wal_status` `reserved` | Slot missing or inactive (WAL retains forever) | `make verify-cdc` |
| Geometry | `nus.trips.route` is in `column.exclude.list` | Geometry on a CDC topic (Debezium `DataException`) | `d-infra-debezium/connectors/nus-pg.json` |
| Topics | `cdc.drivers`, `cdc.passengers`, `cdc.trips`, `cdc.city_zones`, `cdc.vehicles`, `cdc.trip_ratings`, `cdc.driver_sessions` | `no cdc.* topics yet` after `make up` has finished `cdc-register` | `make verify-cdc` |

Patroni physical slots (`nus_pg_*`) are replica slots, not this bar.

#### `e-infra-clickhouse`

Cluster `nus` / `nus_cluster`: 2 shards × 2 replicas. Services and dashboards
query Distributed names (`nus.trip_events`, `nus.driver_positions`, …), never
`*_local`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Members | Four rows in `system.clusters` for `nus_cluster` | Fewer than four | `make verify-ch` |
| Tables | `nus` database lists Distributed + `*_local` + MVs from `e-infra-clickhouse/ddl/` | Missing `trip_events` or `driver_positions` | `make verify-ch` |
| Replication | `absolute_delay` at or near 0 | Delay only growing | `make verify-ch` |

#### `f-infra-grafana`

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

#### `g-infra-superset`

Analytical BI on the same ClickHouse cluster. Empty panels before data are
fine; an HTTP error is not.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Health | `/health` OK | Error page | `make superset-health` |

#### `h-bootstrap`

One-shot: migrations, LION restore, people, history, warehouse load, then
`system:bootstrap:done`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Marker | Redis `system:bootstrap:done` is `1` | `(not set)` | `make verify-data` |
| OLTP seed | `drivers` = `SEED_DRIVERS`; `passengers` = `SEED_PASSENGERS`; `city_zones` = 263 (TLC); `ways` in the hundreds of thousands | `ways` = 0 (`SKIP_MAP_IMPORT` or failed restore) | `make verify-data` |
| Warehouse seed | After bootstrap, `nus.trip_events` count is at least `HISTORY_DAYS × HISTORY_TRIPS_PER_DAY` (one warehouse row per seeded trip). Live traffic then adds lifecycle rows. | 0 events after a finished `make up` | `make verify-data` |
| History routes | Seeded trips that have a `route` are on-network (same 75 m / long two-point bars as §9.4), produced by `nus_common.routing.route()` | Unsnapped harbour points or two-point chords in history | `z-config/check-on-network.sql` (window is last hour for live; all-time `route IS NOT NULL` is the history form) |

#### `i-service-cache-updater`

Applies `cdc.*` to Redis. Does not wait for bootstrap.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Lag | `make lag` group `cache-updater`: LAG near 0 after snapshot drain | LAG only growing | `make lag` |
| Keys | db 1 and db 2 non-zero after snapshot | db 1 = 0 | `make verify-data` |

#### `j-service-driver`

One process, many drivers. Positions go to Kafka `driver_location`, not a
Postgres tick table. `drivers.last_lat` / `last_lon` are last-known, synced
on a timer.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Movement | `follow()` walks `linestring_vertices` of Redis WKT or `routing.drive_path`; empty path and `is_chord_path` path do not lerp the chord | Euclidean hop between two ends | `make verify-walk` (`j-service-driver/tests`) |
| Live polyline | Latest tick: `path_p50` is not 2 while `online` > 0. `path_chord` is the count of online paths with ≤ 2 vertices (short real LION blocks allowed). | `path_p50` = 2 with a live fleet | `docker logs driver-service` tick JSON (`path_p50`, `path_chord`); `make verify-positions` |
| Stream | `nus.driver_positions` `newest` within a minute of now on a live stack | Table empty while the service is up | `make profile` section 1 |

#### `k-service-passenger`

Riders. Independent of driver-service. Requests on `trip_requests`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Cache wait | Process waits for `passenger:*` then produces | Group missing forever while db 2 is filled | `make lag` group `passenger-service`; `make verify-data` db 2 |
| Demand | `trip_requests` LOG-END-OFFSET increases at about `TRIP_REQUESTS_PER_MINUTE` | Topic stays 0 after passenger-service is running | `make lag` group `dispatch-service` on `trip_requests` |

#### `l-service-dispatch`

Match, pgRouting, fare, trip status. **Redis `trip_active` (route WKT)
before Kafka announce.**

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Order | `store_live_state` then `announce` on match and on each non-terminal status | Kafka `matched` / `in_progress` with empty `trip:{id}:active` (driver skips `follow()`, walks an idle chord) | Source `l-service-dispatch/dispatch_service/__main__.py`; live: `make verify-positions` |
| On-network assignment | Last-hour trips with `route IS NOT NULL`: `pickup_off_network` = 0, `dropoff_off_network` = 0, `long_two_point_routes` = 0; `route_km` > `chord_km` when `trips` > 0 | Any off-network count > 0, or long two-point routes > 0 | `make verify-positions` |

#### `m-service-city`

Hotspots and `segment_traffic` updates.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Process | Container not restart-looping; lag on `driver_location` may be tens of seconds, must not only grow | Restart loop; lag only growing | `make lag` group `city-service`; `make errors` |
| Warehouse | `nus.hotspot_history` `newest` current on a live stack | Empty while city-service is up | `make profile` section 1 |

`hot_zones: 0` at 40 req/min over 263 zones is expected (0.6 cutoff). Not a fail.

#### `n-service-clickhouse-sink`

Kafka → Redis enrich → ClickHouse Distributed tables.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Freshness | `make profile` section 1: `trip_events` and `driver_positions` `behind_min` small on a live stack | `newest` stuck | `make profile` |
| Lag | `make lag` group `clickhouse-sink` returns toward 0 after a burst | Lag only growing | `make lag` |
| Live rows | After live traffic, `rows_per_trip` on `trip_events` > 1 (lifecycle walk). Bootstrap alone is one row per seeded trip. | Live stack with `rows_per_trip` = 1 forever | `make profile` section 2 |

#### `o-service-archiver`

Deletes OLTP trips with `ended_at` older than `ARCHIVER_RETENTION_HOURS`.
Analytics remain in ClickHouse.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Backlog | `should_have_been_pruned` = 0: `ended_at IS NOT NULL AND ended_at < now() - ARCHIVER_RETENTION_HOURS` | Growing leftover terminal trips | `make psql` (write proxy): `SELECT count(*) FROM trips WHERE ended_at IS NOT NULL AND ended_at < now() - interval '24 hours';` (use the configured hours) |

#### `z-config`

Stack HAProxy, assessment SQL, `profile.sql`.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| Tiles routing | Frontend `grafana_fe` ACL `path_beg /tiles/` → backend `tiles`; rewrite `^/tiles/(.*) /\1` is on the **backend**, not the frontend | Strip on the frontend (tiles 302/404 from Grafana) | `z-config/haproxy/haproxy.cfg.template`; `make tiles-health` |
| On-network SQL | `z-config/check-on-network.sql` is what `make verify-positions` runs | Different thresholds in this file than in that SQL | §9.4 and `test_assessment_standard.py` |
| Positions SQL | `z-config/check-positions.sql` is diagnostic (not a pass/fail bar) | Using `axis_share` as a gate | §2 |

#### `z-lib`

`nus-common`: ids, geo, routing, Redis keys, clients.

| Bar | Pass | Fail | Instrument |
| --- | --- | --- | --- |
| IDs | `drv-` width 11, `psg-` width 11, `trp-` width 21 | ClickHouse `FixedString` rejects the id | `z-lib/nus-common/nus_common/ids.py`; `make verify-walk` |
| Chord | `is_chord_path(..., min_km=0.2)` is true iff the path has exactly two points ≥ 0.2 km apart | Treating a short two-vertex LION block as a chord | `make verify-walk` |
| Route geometry | `ROUTE_GEOMETRY_SQL` orients edges (`ST_Reverse` when traversed target→source) then `ST_MakeLine` in visit order | `ST_LineMerge(ST_Collect)` without path order | `z-lib/nus-common/nus_common/routing.py`; live `make verify-positions` |

### 9.4 Constraint surface (M1) — a cab is not a boat

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
§9.4 off-network and long-two-point all zeros.

Fail: `path_p50` = 2 on a live fleet; `long_two_point_routes` > 0;
`verify-walk` non-zero.

### 9.5 Generation, ids, time, tiles

Default small seed used to prove this file: `SEED_DRIVERS=800`,
`SEED_PASSENGERS=5000`, `HISTORY_DAYS=1`, `HISTORY_TRIPS_PER_DAY=2000`,
`TRIP_REQUESTS_PER_MINUTE=40.0`, `SKIP_MAP_IMPORT=false`. Those values are
`.env` knobs, not additional bars.

From `z-lib/nus-common/nus_common/ids.py` (ClickHouse `FixedString` matches):

| Entity | Prefix / shape | Length |
| --- | --- | --- |
| Driver | `drv-` + 7 digits (`drv-0000001`) | 11 |
| Passenger | `psg-` + 7 digits (`psg-0000001`) | 11 |
| Trip | `trp-` + `YYYYMMDD` + `-` + 8 hex (`trp-20250824-a1b2c3d4`) | 21 |

`zone_id` is TLC LocationID (`"1"`..`"263"`), not minted here (S6).

Stored timestamps are UTC (`TZ=UTC`, ClickHouse `DateTime64(3, 'UTC')`).
Demand, `day_period`, and TLC `zone_weight` use `SIM_TIMEZONE`
(version-1 default `America/New_York`) via `nus_common.geo.in_sim_tz` /
`sim_zoneinfo`.

Browser tiles: `/tiles/styles/nus/{z}/{x}/{y}@2x.png` (and the 1× PNG).
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

## 10. Command index (version 1)

Assessment commands named in this file (must exist as Makefile targets
under `not-uber-service/`):

| Command | What it scores |
| --- | --- |
| `make verify` | §9.3 bundle (pg, redis, kafka, ksqldb, cdc, ch, dash, data) |
| `make verify-pg` | `a-infra-postgres` |
| `make verify-redis` | `b-infra-redis` |
| `make verify-kafka` | `c-infra-kafka` |
| `make verify-ksqldb` | `c-infra-kafka` |
| `make verify-cdc` | `d-infra-debezium` |
| `make verify-ch` | `e-infra-clickhouse` |
| `make grafana-health` | `f-infra-grafana` |
| `make tiles-health` | `f-infra-grafana`, §9.5 |
| `make grafana-check` | `f-infra-grafana`, §9.5 |
| `make superset-health` | `g-infra-superset` |
| `make verify-data` | S1, G1, G3, G4, `h-bootstrap` |
| `make verify-dash` | grafana + tiles + superset |
| `make verify-positions` | M1, M3, `l-service-dispatch`, §9.4 |
| `make verify-walk` | S5, M1 unit, `j-service-driver` |
| `make lag` | C6, `i-service-cache-updater`, `k-service-passenger`, `m-service-city`, `n-service-clickhouse-sink` |
| `make profile` | S9, M2, M5, `j-service-driver`, `m-service-city`, `n-service-clickhouse-sink` |
| `make help` | `make up` does not rebuild; destroy keeps LION + OSM tiles |
| `make build` | Images after a code change |
| `make destroy` | Data gone; LION + OSM tiles kept |
| `make psql` | `o-service-archiver` |
| `make errors` | `m-service-city` restart / OOM |

SQL and pytest the bars cite:

| Path | Role |
| --- | --- |
| `z-config/check-on-network.sql` | 75 m, `ST_NPoints` ≤ 2, 1000 m |
| `z-config/check-positions.sql` | Diagnostic only (§2) |
| `z-config/profile.sql` | Warehouse freshness and ranges |
| `z-lib/nus-common/nus_common/geo.py` | `is_chord_path` `min_km=0.2` |
| `z-lib/nus-common/nus_common/ids.py` | `drv-` / `psg-` / `trp-` |
| `z-lib/nus-common/tests/` | `make verify-walk`; this contract |
| `j-service-driver/tests/` | polyline walk vs chord |
| `h-bootstrap/migrations/` | S2–S10 |
| `c-infra-kafka/schemas/` | B1 |
| `c-infra-kafka/topics/topics.tsv` | B2, B4 |
| `e-infra-clickhouse/ddl/` | S11–S16 |
| `d-infra-debezium/connectors/nus-pg.json` | S10, B5 |

---

## 11. Inheritance

A new studio version:

1. Keeps §§1–8 unchanged.
2. Adds a directory next to `not-uber-service/` and a profile section in
   this file with the same table shape as §9: role mapping, studio-bar
   bindings, named instruments, named pass, named fail. Numbers come from
   that version’s shipped SQL, Avro, DDL, and tests — not from a
   screenshot.
3. Names the constraint surface (M1) for that domain. Ride-hail is
   distance-to-`ways`. A catalog domain is membership in the catalog. A
   schedule domain is a feasible leg. The test is numeric or set-valued.
4. Extends `test_assessment_standard.py` (or the version’s equivalent) so
   closed sets, id widths, topic lists, and Makefile target names cannot
   drift.

Version 1 is closed for this file when `make verify`, `make tiles-health`,
`make verify-walk`, and `make verify-positions` (last-hour `trips` > 0)
all pass the tables in §9, and the pytest in `z-lib/nus-common/tests/`
exits 0.
