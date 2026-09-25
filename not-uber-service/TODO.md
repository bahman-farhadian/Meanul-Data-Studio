# not-uber-service — finalization plan

One step at a time. A step is finished when its **Done when** line is true,
not when the code is written. Do not start step N+1 with step N red.

Each step says where it is tested:

- **LOCAL** — unit tests (`make verify-walk`) or the throwaway sample stack
  (`make verify-sample`). No Dionysus time.
- **DIONYSUS** — needs the real stack. Costs server hours; batch the
  local work first.

## Test protocol on Dionysus

**Every DIONYSUS step starts from a clean stack and ends by tearing it
down.** A step is never measured on state left behind by the step before
it — otherwise a pass can come from stale containers, stale data, or a
fix that was built and never actually running. That failure has already
happened here once.

The cycle:

```
make destroy
make init
make up
make etcd-existing
```

`make destroy` keeps the two slow artifacts on purpose — the LION routable
graph and `nyc.mbtiles` — so a fresh start needs no internet. It *does*
remove the volume directories, which is why `make init` runs again before
`make up`; `make preflight` fails with "volume directories the stack mounts
do not exist" when that is skipped.

Skip `make prepare` unless preflight says an image, the LION dump, or the
MBTiles file is actually missing.

Evidence: every step records the commands run and the output that proved
its **Done when** line — measured after the bring-up that produced the
data, never from an earlier run.

Future major-version ideas live in FUTURE_ROADMAP.md, deliberately out of
this file.

---

## Where this stands (reviewed 2026-09-25)

**Solid already.** Patroni/etcd OLTP, Sentinel Redis, KRaft Kafka + Schema
Registry + Debezium, 2x2 ClickHouse + Keeper, HAProxy entry tier, o-service
archiver, self-hosted OSM tiles, five provisioned Grafana dashboards with a
validator, 23 unit tests, and ASSESSMENT.md — a contract whose numbers are
read back out of the shipped sources by `test_assessment_standard.py`, so
the document cannot drift silently.

**Schema is further along than the concern suggested.** OLTP has drivers,
passengers, trips, vehicles, trip_ratings, driver_sessions, city_zones,
segment_traffic, and two real TLC calibration tables — with CHECK
constraints, foreign keys, PostGIS geometry, jsonb + GIN, and spatial
indexes. The warehouse has trip_events, driver/rider positions, hotspot and
segment-traffic history, plus hourly/daily rollups and AggregatingMergeTree
percentile/uniq views, all Distributed over `_local`.

**The real gaps are these, and steps 2–11 close them:**

| Gap | Why it matters |
| --- | --- |
| No driver-arrival timestamp | Rider wait time and post-arrival cancellation — two core ride-hail metrics — are not computable from the data at all |
| Payments are two columns on `trips` | Real platforms model payment as its own record (method, amount, status, refunds, adjustments); ours cannot express a failed or refunded charge |
| `trips` is one unpartitioned table | At 7 days x 655k/day the archiver deletes by row instead of dropping a partition — bloat and vacuum pressure at exactly the scale we intend to prove |
| `driver_positions`: monthly partition, 3-day TTL | TTL deletes inside parts rather than dropping partitions; at full-fleet tick rate this is the heaviest table in the stack |
| ksqlDB is empty | Deployed and authenticated, zero streams registered — DBeaver connects and correctly shows nothing. The only bar today is "server RUNNING" |
| Superset has no dashboards | `g-infra-superset/init/` registers connections only. The analytical-BI tier is an empty shell |
| No marketplace KPIs | Fulfillment rate, cancellation rate, rider wait time, take rate, surge effectiveness — none are panels today |
| Generation quality is unscored | Bars prove rows exist and align; nothing scores distributions, null rates, or fidelity to the TLC calibration |

---

## Step 1 — Close the open street-following check

The live data-quality item carried over from 2026-09-18. driver-service and
dispatch-service now walk pgRouting geometry instead of Euclidean chords;
that fix has never been confirmed against live data.

Run the clean cycle from the test protocol above, at the dev seed already
in `.env` (800 drivers, 1 day, 2000 trips/day, 40 req/min). A fresh
bring-up creates every container from the current images, so the walk code
is live by construction — nothing to rebuild or reload here.

```
make destroy
make init
make up
make etcd-existing
```

Then let live traffic run long enough to complete trips before measuring:
`check-on-network.sql` filters on `ended_at > now() - interval '1 hour'`,
so the window must contain finished trips produced by this bring-up.

**Note:** the old instruction here said to confirm with `make profile`
section 10b (`diagonal_pct`). That column does not exist anywhere in the
repo — section 10b emits `axis_share` / `both_axes_pct`, and ASSESSMENT.md
§9.2 classifies both as explicit **not-bars** (Manhattan's grid is rotated
~29°, so ~0.71 is expected on a real polyline). Use the real instruments
below instead.

**Test:** DIONYSUS — `make verify-positions`; `docker compose logs driver-service --tail 20 | grep tick`

**Done when:** on positions produced by this bring-up,
`pickup_off_network` = 0, `dropoff_off_network` = 0,
`long_two_point_routes` = 0, `route_km` > `chord_km` with last-hour
`trips` > 0, and the driver tick shows `path_p50` ≠ 2 while `online` > 0.

### DONE — 2026-09-25, Dionysus

Fresh bring-up at the dev seed. `make verify-positions`, on containers
started 15:32:21Z (driver) / 15:32:22Z (dispatch):

```
 trips | pickup_off_network | dropoff_off_network | long_two_point_routes
   245 |                  0 |                   0 |                     0

 route_km | chord_km | avg_vertices
     4.14 |     3.06 |          100
```

`docker compose logs driver-service | grep tick`: `online` 486-515,
`path_p50` 125-171, `path_chord` 0-3.

Every criterion met. `avg_vertices` 100 and `axis_share` 0.715-0.727 (the
~0.71 §9.2 predicts for a real polyline on Manhattan's rotated grid)
corroborate it; both are not-bars, not gates.

**Defect found during this run — fixed, needs confirming on the next
bring-up.** `make verify` went red on tiles. The stack was fine: the same
`/tiles/styles.json` returned 200 through the same HAProxy from `::1`,
from the LAN address, from lb-a's container IP, and from inside
nus-backbone — and empty only from `127.0.0.1`, the single address
`tiles-health` hardcoded. Docker's published-port DNAT cannot send a
loopback destination to a real interface while `route_localnet` is 0, so
docker-proxy accepts and closes. `_wait-haproxy-pg-settled` had the same
shape and survived only because `localhost` resolves to `::1` here.

Both now read through nus-backbone, with a pytest guard
(`test_health_checks.py`) that fails if either pattern returns. The
HAProxy `timeout tunnel` warning the same investigation exposed is gone
too. Re-run `make verify` on the next bring-up to confirm green — the
HAProxy change needs a re-render, so it only takes effect then.

---

## Step 2 — Schema design review (writing only, no code)

Produce `docs/schema-review.md`: every finding below argued accept or
reject, with a reason. This is the step that decides what steps 3–5 build,
so it is worth doing carefully and it costs no server time.

Reviewed against how real ride-hail platforms model this domain (a trip is
8–10 state transitions; payment is its own entity; telemetry streams to the
warehouse, never to OLTP):

1. **`arrived_at` on trips** — driver reached pickup. Without it, rider wait
   time and "cancelled after driver arrived" cannot be derived. Also implies
   an `arrived` lifecycle symbol across all three stores.
2. **`payments` table** — one row per charge attempt: trip, method, gross,
   platform fee, driver payout, status, timestamps. Today `payment_method`
   and `driver_payout` sit on `trips` and cannot express failure or refund.
3. **Partition `trips` by month on `requested_at`** — lets the archiver
   drop partitions instead of deleting rows at full scale.
4. **Driver accept / decline** — dispatch assigns directly today; real
   platforms offer and the driver may decline, which is where acceptance
   rate comes from. Decide explicitly whether this simulation wants it.
5. **Cancellation fee** — a real consequence of cancelling after arrival;
   only expressible once (1) exists.
6. **`drivers.rating` / `passengers.rating`** — denormalized aggregates of
   `trip_ratings`, recomputed in bulk. Confirm that is intended and
   documented rather than accidental.

**Test:** LOCAL — review only.

**Done when:** every item above is accept/reject with a written reason, and
accepted items have a named target table/column.

---

## Step 3 — OLTP schema changes

Migrations `013_*.sql` onward, one concern per file, for whatever step 2
accepted. Existing migrations are never edited.

**Test:** LOCAL `make verify-sample` first (the sample stack applies real
migrations), then DIONYSUS `make destroy && make up`.

**Done when:** migrations apply on an empty database, `make verify-data`
passes, and no existing bar regressed.

---

## Step 4 — Three-copy alignment

Any closed set or id added in step 3 must land in all three forms at once:
Postgres `CHECK`, the `.avsc` enum, and the ClickHouse `Enum8` — same
symbols, same spelling. Extend `test_assessment_standard.py` so the new
sets and any new topic cannot drift either.

**Test:** LOCAL — `make verify-walk`.

**Done when:** pytest exits 0 with the new sets covered, and
`make verify-ch` shows the altered tables on all four members.

---

## Step 5 — Services write the new fields

dispatch-service records arrival and the payment row; clickhouse-sink maps
the new Avro fields to the new warehouse columns; generators populate
whatever step 2 accepted. B7/M3 order still holds — durable write and cache
write before the Kafka announce.

**Test:** LOCAL `make verify-sample`; then DIONYSUS `make profile`.

**Done when:** the new columns are populated at the expected rate in
`make profile` (not silently NULL), and `make verify-positions` is still
all zeros.

---

## Step 6 — Warehouse fitness for full scale

The heaviest table decides whether the full-scale run survives. At full
fleet, `driver_positions` dominates everything else in the stack.

- Re-partition `driver_positions` (and `rider_positions`) so the TTL drops
  whole partitions instead of deleting inside monthly parts.
- Check `ORDER BY (driver_id, event_time)` against what the dashboards
  actually ask — live-ops queries a time window across all drivers, which
  that key does not serve well. Add a skip index or reconsider the key.
- Measure real bytes/row at mid scale and project the full-scale footprint
  against the 96 GB per-node quota, with headroom.

**Test:** DIONYSUS — measure ingest rate and on-disk growth over a fixed
window; extrapolate.

**Done when:** the projection fits the quota with stated headroom, written
down in the ClickHouse README, and TTL drops partitions rather than rows.

---

## Step 7 — Data-collection quality bars

Today's bars prove rows exist and align. None score whether the generated
data is any *good*. Add numeric bars (ASSESSMENT §7 already has the frame):

- Null rate per nullable column, with a ceiling.
- Referential integrity: zero orphan rider/driver/trip references.
- Distribution sanity: fare, duration, distance within stated ranges; no
  single-value columns where variety is intended.
- Calibration fidelity: generated demand per zone/hour correlates with
  `zone_demand_calibration` above a stated threshold — the one bar that
  proves generation actually uses the real TLC surface.

**Test:** DIONYSUS — `make profile` plus new SQL under `z-config/`.

**Done when:** each bar has a numeric pass line in ASSESSMENT.md and the
current run meets it.

---

## Step 8 — ksqlDB streams (fixes "DBeaver shows nothing")

ksqlDB exists so a client can run SQL over live topics; nothing has ever
been registered in it. Add a `CREATE STREAM` per declared Avro topic
(`driver_location`, `rider_location`, `trip_requests`, `trip_lifecycle`,
`city_hotspots`, `segment_traffic_updates`) with `VALUE_FORMAT='AVRO'`,
applied by a one-shot the way `make ch-ddl` applies ClickHouse DDL, wired
into `make up`. Upgrade `verify-ksqldb` so an empty `SHOW STREAMS` **fails**
instead of passing.

**Test:** DIONYSUS — `make verify-ksqldb`, then DBeaver.

**Done when:** `SHOW STREAMS` lists all six, a `SELECT ... EMIT CHANGES`
returns live rows, and DBeaver shows them without hand-created objects.

---

## Step 9 — Grafana: the missing marketplace KPIs

Existing dashboards cover live ops, driver/trip inspectors, and history
well. Missing the metrics a real marketplace is actually run on:

- Fulfillment rate (requests → completed) and cancellation rate by reason.
- Rider wait time — request → pickup (needs step 3's `arrived_at`).
- Take rate: platform fee vs driver payout vs gross.
- Surge effectiveness: surge multiplier vs subsequent fulfillment in-zone.
- ETA accuracy: predicted vs actual, already stored, never charted.

Panels are generated by `render_dashboards.py` and must pass
`check-dashboards.py` (Distributed tables only, uid `nus-clickhouse`).

**Test:** LOCAL `check-dashboards.py`; DIONYSUS `make grafana-check` + eyes.

**Done when:** the check passes and every new panel renders non-empty
against live data.

---

## Step 10 — Superset: the analytical BI tier

Currently an empty shell. Provision charts and dashboards declaratively
(Superset's own asset import, the same reproducibility standard
`register_database.py` already set — not hand-clicking the UI), reading the
warehouse rollups, not raw event tables. Add a validator mirroring
`check-dashboards.py`, and a bar stronger than "/health answers".

**Test:** DIONYSUS — import onto a *fresh* Superset, then `make verify-dash`.

**Done when:** assets import clean from a cold start and every chart
returns rows.

---

## Step 11 — Contract and documentation

- ASSESSMENT.md: bars for ksqlDB streams, Superset assets, the step-7
  quality numbers, and any new closed set — each with instrument, pass,
  fail.
- Extend `test_assessment_standard.py` to read them from the sources.
- Schema documentation: ERD plus a per-table, per-column data dictionary
  for both stores, generated from the live schema so it cannot rot.

**Test:** LOCAL — `make verify-walk`.

**Done when:** pytest exits 0 and the docs match what is actually shipped.

---

## Step 12 — Staged scale-up

Do not jump from the dev seed to full scale. Two measured stops, each a
full `destroy` + `up`, fixing what breaks before moving on:

1. ~10% of target fleet, 1 day of history.
2. ~50%, 2–3 days of history.

Watch at each stop: bootstrap wall-clock, Postgres memory (the pgr_ksp
OOM history), consumer lag returning to zero, ClickHouse disk growth vs
quota, and `verify-positions` staying at zeros.

**Test:** DIONYSUS.

**Done when:** both stops pass the full verify suite with no OOM, lag
recovering, and disk within quota — and the extrapolation to full scale is
written down.

---

## Step 13 — Full-scale final run

The target agreed at the start of the project: **7 days of history at real
New York City scale** — `SEED_DRIVERS=106000`, `SEED_PASSENGERS=1500000`,
`HISTORY_DAYS=7`, `HISTORY_TRIPS_PER_DAY=655000`,
`TRIP_REQUESTS_PER_MINUTE=455.0`, `SKIP_MAP_IMPORT=false` — the values
already in `.env.example`.

Before starting, restore the live `.env` from the dev seed to those values
(they are existing keys; `make init` will not change them — edit them).

Then the full suite: `make verify`, `make verify-walk`,
`make verify-positions` (last-hour trips > 0), `make tiles-health`,
`make grafana-check`, `make profile`, `make lag`, plus the step-7 quality
bars and the step-10 Superset check.

**Test:** DIONYSUS.

**Done when:** every bar in ASSESSMENT.md §9 passes at full scale — which
is that file's own definition of version 1 being closed.

---

## Parked (measured, revisit only at full scale)

**Replica scaling — cache-updater / clickhouse-sink.** Both already share
`KAFKA_GROUP_ID` and have no fixed `container_name`, so
`docker compose up --scale N` works; rebalancing across two instances was
confirmed live. Measured on Dionysus 2026-09-17 at the dev seed:
clickhouse-sink 2.9% CPU / 56 MiB, cache-updater 0.6% / 57 MiB. One replica
each is enough. Revisit if `driver_location` lag grows during step 12–13.

**Sharding driver-service / passenger-service.** Both are one synchronous
Python process; a lone instance cannot exceed about one core of Python
bytecode. Measured on the same run: 0.13% CPU each. One process is enough
at this seed. Revisit only if step 12 shows either pegged.

**Connector note.** `nus.trips.route` stays excluded from Debezium (PostGIS
geometry Debezium cannot serialize) — already in `nus-pg.json`.
