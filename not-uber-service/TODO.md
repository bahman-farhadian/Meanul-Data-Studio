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

## Working blocks

The thirteen steps are worked in four blocks, not one at a time. Steps
that read the same stack are verified on one bring-up rather than on one
each - eight remaining steps become four cycles.

| Block | Steps | Where | Why they combine |
| --- | --- | --- | --- |
| A | 6 (measure) + 7 | Dionysus, one bring-up | Both read the same live stack: measure bytes/row while the quality bars run |
| B | 8 + 9 + 10 | Dionysus, one bring-up | ksqlDB, Grafana and Superset all read the same warehouse tables - build all three, verify once |
| C | 11 | Local | Contract and documentation. No server time |
| D | 12 + 13 | Dionysus | The staged scale-up running straight into the full-scale run |

Step 11 is deliberately placed between B and D rather than last: writing
down what the contract actually is, immediately before the full-scale
run, is when the last drift gets caught.

**Blocks A to C run at dev scale, and deliberately.** Correctness is what
they test, and correctness is scale-independent. Four things are not, and
must never be "fixed" on the strength of a dev-scale reading:

- **fulfilment rate and surge** - wrong at dev scale because the
  supply/demand ratio is wrong, not because the code is. See step 12.
- **the disk projection** - bytes/row is measurable now; the
  extrapolation from it is the risk, and only step 13 settles it.
- **city-service consumer lag** - 2,976 behind at 800 drivers says
  nothing reliable about the shape at 106,000.
- **merge pressure and TTL partition drops** - these only appear at
  volume.

This has a direct consequence for step 7: a quality bar must be
STRUCTURAL, not distributional. "Zero orphans", "zero negative lags",
"rows = uniqExact(event_id)", "no party of six outside xl" hold at every
scale. "Fulfilment above 0.8" passes at one scale and fails at the other,
which makes it a bar that measures the seed settings rather than the
pipeline.

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

**The real gaps are these, and steps 2–11 close them.** Step 2 is now
done and `docs/schema-review.md` argues each schema gap against a real
source; the finding ids below point into it.

| Gap | Why it matters |
| --- | --- |
| Four `trips` columns never leave Postgres (F1) | `driver_payout`, `payment_method`, `cancellation_reason`, `requested_vehicle_type` are in no `.avsc` and no ClickHouse DDL — so take rate, payment mix, why trips cancel, and anything per tier are unanswerable in the only store Superset may read |
| Money is `Float64` in ClickHouse (F3a) | Float addition is not associative and SummingMergeTree sums `revenue` during background merges in an uncontrolled order, so the total depends on merge history — measured at ~1e-9 relative, so sub-cent, but it never reconciles against Postgres's `numeric(10,2)` and carries a float tail onto every dashboard |
| No `event_id` on any event (F6) | A replayed sink batch is indistinguishable from a genuine repeated status, and `trip_events_local` is a plain ReplicatedMergeTree that will not dedupe it. Also fails our own §3.3 correlation-id rule |
| Every rollup filters `completed` (F7) | Cancellations and unmatched requests appear in no aggregate we produce — fulfilment rate, the most basic health metric, needs a self-join over a year of raw events |
| No driver-arrival timestamp (F2) | Rider wait time and post-arrival cancellation — two core ride-hail metrics — are not computable from the data at all |
| A driver can never decline (F5) | Dispatch assigns directly, so acceptance rate, offers per match and time-to-match do not exist. Real platforms offer with a deadline and re-offer on decline. **Decided 2026-09-25: in scope, in full** |
| Payments are two columns on `trips` (F4) | Real platforms model payment as an append-only record (method, amount, status, refunds, adjustments); ours cannot express a failed or refunded charge, and overwriting the column would destroy the history |
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

## Step 2 — Schema research and improvement proposals (writing only)

DONE — 2026-09-25, local. Deliverable: `docs/schema-review.md`.

Not a review of a schema already assumed sound. The question asked was:
where is this schema weak against how real ride-hail platforms actually
model the domain, and what concretely closes the gap. Section 1 of the
deliverable lists every source with what it contributed — Uber's own
engineering and product material (H3, upfront pricing, the guest-rides
dispatch states, Schemaless), a complete 14-table reference model, the
dispatch/acceptance literature, marketplace-ledger practice, Kafka
envelope practice, Altinity on ClickHouse money types, and Kimball on
fact grain.

Fourteen findings, F1–F14. Three of them end in "keep what we have", with
the reason. The rest are ordered into three tiers in section 4:

**Tier 1 — steps 3–5 build these.** F1 four columns Postgres knows never
reach Kafka or ClickHouse (take rate, payment mix, cancellation reason,
vehicle tier are all unanswerable in the warehouse) · F3a money is
`Float64` in ClickHouse and `double` on the wire, and SummingMergeTree
sums it during merges in an uncontrolled order, so revenue never
reconciles exactly against Postgres ·
F6 no `event_id` or correlation id on any event, so a replayed batch is
indistinguishable from real repeated status · F7 no trip-grain fact, so
every rollup filters `completed` and cancellations are invisible in every
aggregate we produce · F2 no `arrived` state, so wait time and
post-arrival cancellation cannot be derived · F5 a driver can never
refuse, so the whole matching funnel is unmeasurable · F11
`passenger_count` is produced on the wire and discarded.

**Tier 2 — if steps 3–5 come in under budget.** F3b `fare_components` ·
F4 append-only `payments` and `driver_earnings` · F8 Type 1 dimensions
from the existing CDC topics · F14 `trip_ratings` into the warehouse.

**Tier 3 — recorded, not built for v1.** F9 H3 as an additive second
spatial key · F12 calibration provenance · F10 driver documents.

**Rejected, with reasons written down:** the `ride_requests`/`trips`
split, a full double-entry ledger, Kimball Type 2 dimensions, a Postgres
`surge_multipliers` table, integer minor units for money.

**Test:** LOCAL — writing only, no server time.

**Done when:** every finding is accept or reject with a written reason and
a named target table/column, and each one cites the source it came from. ✔

**Decisions taken 2026-09-25** (section 6 of the review):

- **F5 is in scope, in full.** A driver may accept, decline, or let an
  offer expire, and the request moves to the next candidate. Promoted
  from tier 2 to tier 1.
- **F6 goes on all six topics**, position streams included. No
  measurement gate. ClickHouse does not guarantee deduplication —
  ReplacingMergeTree dedupes only eventually, only within a partition,
  only on merge — so uniqueness must be guaranteed before the warehouse,
  and only then is aggregating inside ClickHouse trustworthy. The byte
  cost is a capacity input to step 6, not a reason to narrow the scope.
- **Money is two decimals everywhere.** `numeric(10,2)` in Postgres,
  Avro `decimal(10,2)` on the wire, `Decimal64(2)` in ClickHouse. No
  `double` hop in the middle.
- Commit `8b5849d`'s non-imperative subject is left as it is.

---

## Step 3 — OLTP schema changes

DONE — 2026-09-26, Dionysus. Verified on a full destroy/build/up cycle
with live traffic running, not on seeded rows alone:

- **Uniqueness** — `rows` = `unique_events` on all five warehouse tables
  (trip_events 6,326, dispatch_offers 5,356, driver_positions 253,365,
  rider_positions 38,406, hotspot_history 19,712), zero malformed ids.
  This is the bar every other count depends on.
- **The `arrived` state is live** — 591 events, `reached_the_kerb` 2,903,
  mean rider wait at the kerb 81s, 189 cancellations after arrival.
  `driver_positions` now carries `en_route_pickup` as its own status.
- **The funnel is real** — 0.494 acceptance over 2.02 offers per match;
  acceptance falls 0.601 → 0.367 by pickup ETA and 0.541 → 0.347 by
  position in the chain. No position accepts 100% of the time.
- **Milestones** — 2,896 trip_facts rows, 2,896 distinct trips, **zero
  negative lags**.
- **Money** — take rate 0.2300 against PLATFORM_COMMISSION_PCT 0.23,
  summed type `Decimal(38, 2)`.
- **The four orphaned columns** — all populated: driver_payout 1,626,
  payment_method 1,626, cancellation_reason 596 (matching exactly the
  303 + 293 cancellations), non-default tier 1,115.
- **Seats is a real constraint** — parties of 5 and 6 appear in `xl` and
  in no other tier.
- Every topic carries messages, including `segment_traffic_updates` (534)
  which had never been written to before, and `make errors` reports
  nothing from city-service or cache-updater.

Migrations `013_*.sql` onward, one concern per file. Existing migrations
are never edited. Tier 1 of `docs/schema-review.md` is the scope:

- `013` — `trips.arrived_at`, and `arrived` appended to the status CHECK
  (F2). Append, never renumber: ClickHouse `Enum8` values already on disk
  must stay valid.
- `014` — `trips.passenger_count smallint NOT NULL DEFAULT 1` (F11).
- `015` — `dispatch_offers` (F5): `(offer_id, trip_id, driver_id,
  sequence, offered_at, expires_at, responded_at, status, eta_seconds,
  distance_to_pickup_m)`, `status` CHECK in
  `offered|accepted|declined|expired|cancelled`, unique on
  `(trip_id, sequence)`.
- `016` — `source_month` / `calibrated_at` on both calibration tables
  (F12, Tier 3 but free to carry here).

F1, F3a, F6 and F7 are Kafka and ClickHouse changes, not Postgres ones —
they land in step 4, not here. Tier 2's tables (`dispatch_offers`,
`fare_components`, `payments`, `driver_earnings`) get their own
migrations only once the Tier 1 sequence is green.

**Test:** LOCAL `make verify-sample` first (the sample stack applies real
migrations), then DIONYSUS `make destroy && make up`.

**Done when:** migrations apply on an empty database, `make verify-data`
passes, and no existing bar regressed.

---

## Step 4 — Three-copy alignment

DONE — 2026-09-26, Dionysus. Verified on a full destroy/build/up cycle
with live traffic running, not on seeded rows alone:

- **Uniqueness** — `rows` = `unique_events` on all five warehouse tables
  (trip_events 6,326, dispatch_offers 5,356, driver_positions 253,365,
  rider_positions 38,406, hotspot_history 19,712), zero malformed ids.
  This is the bar every other count depends on.
- **The `arrived` state is live** — 591 events, `reached_the_kerb` 2,903,
  mean rider wait at the kerb 81s, 189 cancellations after arrival.
  `driver_positions` now carries `en_route_pickup` as its own status.
- **The funnel is real** — 0.494 acceptance over 2.02 offers per match;
  acceptance falls 0.601 → 0.367 by pickup ETA and 0.541 → 0.347 by
  position in the chain. No position accepts 100% of the time.
- **Milestones** — 2,896 trip_facts rows, 2,896 distinct trips, **zero
  negative lags**.
- **Money** — take rate 0.2300 against PLATFORM_COMMISSION_PCT 0.23,
  summed type `Decimal(38, 2)`.
- **The four orphaned columns** — all populated: driver_payout 1,626,
  payment_method 1,626, cancellation_reason 596 (matching exactly the
  303 + 293 cancellations), non-default tier 1,115.
- **Seats is a real constraint** — parties of 5 and 6 appear in `xl` and
  in no other tier.
- Every topic carries messages, including `segment_traffic_updates` (534)
  which had never been written to before, and `make errors` reports
  nothing from city-service or cache-updater.

Any closed set or id added in step 3 must land in all three forms at once:
Postgres `CHECK`, the `.avsc` enum, and the ClickHouse `Enum8` — same
symbols, same spelling. This is also where the Kafka/ClickHouse half of
Tier 1 lands:

- F1 — `driver_payout`, `payment_method`, `cancellation_reason`,
  `requested_vehicle_type` added to `trip_lifecycle.avsc` (optional with
  defaults, so the Registry sees a backward-compatible evolution) and to
  `trip_events_local`.
- F3a — money is two decimals in all three copies: `numeric(10,2)`
  already in Postgres, Avro `decimal` precision 10 scale 2 on the wire
  (replacing `double` in `trip_lifecycle.avsc`), `Decimal64(2)` in
  ClickHouse including `revenue` in the three SummingMergeTree rollups.
  `surge_multiplier` stays `Float64`; it is a ratio, not money.
  **Run the Avro decimal round-trip check first** — serialise
  `Decimal("12.22")` through `AvroSerializer`/`AvroDeserializer` against
  the real Registry and read it back unchanged. fastavro implements the
  logical type, but that is believed, not verified here.
- F6 — `event_id` / `event_version` / `producer` / `correlation_id` on
  **all six** records, and `event_id` carried into every ClickHouse
  table. Decided; not a subset, not gated on a measurement.
- F5 — a `dispatch_offers` Avro record and topic, keyed by `trip_id` so
  the whole offer chain for one request stays ordered on one partition,
  plus `dispatch_offers_local` and a `dispatch_funnel_hourly` rollup.
- F7 — `trip_facts_local` and `fulfilment_hourly`.
- F2/F11 — `arrived` and `passenger_count` mirrored from step 3.

Extend `test_assessment_standard.py` so the new sets and any new topic
cannot drift — and widen it to compare *column names* across the three
stores for the trip entity, not only enum symbols. Comparing symbols is
exactly why F1 went unnoticed.

**Test:** LOCAL — `make verify-walk`.

**Done when:** pytest exits 0 with the new sets covered, and
`make verify-ch` shows the altered tables on all four members.

---

## Step 5 — Services write the new fields

DONE — 2026-09-26, Dionysus. Verified on a full destroy/build/up cycle
with live traffic running, not on seeded rows alone:

- **Uniqueness** — `rows` = `unique_events` on all five warehouse tables
  (trip_events 6,326, dispatch_offers 5,356, driver_positions 253,365,
  rider_positions 38,406, hotspot_history 19,712), zero malformed ids.
  This is the bar every other count depends on.
- **The `arrived` state is live** — 591 events, `reached_the_kerb` 2,903,
  mean rider wait at the kerb 81s, 189 cancellations after arrival.
  `driver_positions` now carries `en_route_pickup` as its own status.
- **The funnel is real** — 0.494 acceptance over 2.02 offers per match;
  acceptance falls 0.601 → 0.367 by pickup ETA and 0.541 → 0.347 by
  position in the chain. No position accepts 100% of the time.
- **Milestones** — 2,896 trip_facts rows, 2,896 distinct trips, **zero
  negative lags**.
- **Money** — take rate 0.2300 against PLATFORM_COMMISSION_PCT 0.23,
  summed type `Decimal(38, 2)`.
- **The four orphaned columns** — all populated: driver_payout 1,626,
  payment_method 1,626, cancellation_reason 596 (matching exactly the
  303 + 293 cancellations), non-default tier 1,115.
- **Seats is a real constraint** — parties of 5 and 6 appear in `xl` and
  in no other tier.
- Every topic carries messages, including `segment_traffic_updates` (534)
  which had never been written to before, and `make errors` reports
  nothing from city-service or cache-updater.

dispatch-service stops assigning and starts offering (F5): one candidate
at a time, with a deadline, `sequence` incrementing down the chain, and a
decline probability that is a real function of `eta_seconds` and surge —
the acceptance literature in the review says pickup time depresses
acceptance and surge raises it, so a flat coin-flip would produce data not
worth charting. An expired offer is a distinct outcome from a declined
one. `no_driver_found` becomes what it should always have been: the end of
an exhausted offer chain, not a decision the generator makes.

The same service emits the `arrived` transition with a realistic dwell
before `in_progress`, and populates the four F1 fields it already computes
onto the `trip_lifecycle` message. passenger-service carries
`passenger_count` through to the trip row, and dispatch refuses a match
where it exceeds the vehicle's `seats`. Every producer stamps the F6
envelope on every message. clickhouse-sink maps the new Avro fields to
the new warehouse columns and rejects any `event_id` it has already seen.
B7/M3 order still holds — durable write and cache write before the Kafka
announce.

**Test:** LOCAL `make verify-sample`; then DIONYSUS `make profile`.

**Done when:** the new columns are populated at the expected rate in
`make profile` (not silently NULL), and `make verify-positions` is still
all zeros.

---

## Step 6 — Warehouse fitness for full scale
DONE — 2026-09-26, Dionysus. `make capacity` reads **63.55 GiB per node
against a 112 GB quota — 56.7%, verdict ok**, with every producer live and
the rates settled over a 30-minute window.

| table | rows/s now | at full scale | TTL | per node |
| --- | --- | --- | --- | --- |
| `driver_positions` | 653 | 17,310 | 2 d | 53.20 GiB |
| `rider_positions` | 16.7 | 442 | 7 d | 5.00 GiB |
| `trip_events` | 1.16 | 30.8 | 30 d | 4.43 GiB |
| `dispatch_offers` | 0.34 | 8.9 | 30 d | 0.69 GiB |
| `hotspot_history` | 8.5 | 8.5 (unscaled) | 30 d | 0.22 GiB |
| `segment_traffic_history` | 0.53 | 0.5 (unscaled) | 30 d | 0.04 GiB |

TTL drops directories rather than rewriting parts: `driver_positions`,
`rider_positions` and `trip_events` each hold two daily partitions.

Whole-stack commitment is 808 GB of the 888 GB on `/dev/nvme1n1p1` (91%) —
kafka 3x96, clickhouse 4x112, postgres 3x24. Kafka measured 842 MB per
broker, roughly 22 GB projected, so it is the slack if ClickHouse ever
needs more. PostgreSQL's 24 GB is still unmeasured.


The heaviest table decides whether the full-scale run survives. At full
fleet, `driver_positions` dominates everything else in the stack.

- Re-partition `driver_positions` (and `rider_positions`) so the TTL drops
  whole partitions instead of deleting inside monthly parts.
- Check `ORDER BY (driver_id, event_time)` against what the dashboards
  actually ask — live-ops queries a time window across all drivers, which
  that key does not serve well. Add a skip index or reconsider the key.
- **MEASURED 2026-09-26, and it does not fit.** `make capacity` on a warm
  dev stack projects roughly 115 GiB per node against a 96 GiB quota, and
  `driver_positions` is ~86 GiB of that on its own: 40.3 bytes/row at
  667 rows/s scaled to ~17,700 rows/s, held for its 3-day TTL.
  `rider_positions` is NOT in that figure - passenger-service had not
  reached its loop when the measurement was taken, so it reads zero and
  the real total is higher. Re-measure with every producer running before
  choosing a lever.
- The levers, with the arithmetic, so the choice is not a guess:
  `DRIVER_TICK_SECONDS` 3 -> 5 takes driver_positions to ~52 GiB; the
  3-day TTL to 2 days takes it to ~57; both together ~34. Cutting
  `trip_events` from 365 days to 180 takes it from 22 GiB to 11. Sampling
  positions rather than storing every report is the fourth option and the
  only one that changes what the data can answer, so it is the last
  resort rather than the first.
- Target is 67 GiB per node, which is the 30% headroom line. A warehouse
  planned to exactly fill its disk cannot merge.
- **DONE 2026-09-26: the five 365-day TTLs are now 90 days**, on the
  measured argument that seven days of history does not need a year of
  retention. That takes 32.5 GiB per node down to 8.0.
- **STILL OPEN: the projection is 90.6 GiB against a 96 GiB quota** - 94%,
  inside the quota but far past the 30% headroom line. The driver tick
  stays at 3 seconds by decision, so `driver_positions` remains 79 GiB of
  it, which is 87% of the whole remaining figure. One of these closes it:
  - position TTL 3 days -> 2 gives 52.8 GiB there and 64.2 total (67%,
    passes). 002_positions.sql's own comment already invites exactly this
    re-check, and says the live map reads Redis rather than this table.
  - position TTL 3 days -> 1 gives 26.4 and 37.8 total (39%, ample).
  - raise the ClickHouse quota above 96 GB/node, if the data disk has the
    room. Not yet checked - `df -h` on NUS_VOLUME_ROOT is the input.
- **DONE 2026-09-26: option C.** ClickHouse quota 96g -> 112g per node and
  the position TTL 3 days -> 2. That is 58.8 GiB against 112 (52%) and
  808 GB of 888 committed across the whole stack (91%).

### Whole-stack storage, measured

`NUS_VOLUME_ROOT` is on `/dev/nvme1n1p1`, 888 GB. Committed quota after
option C: kafka 3x96 = 288, clickhouse 4x112 = 448, postgres 3x24 = 72,
total 808 GB = 91% of the disk. An XFS project quota caps a directory, it
does not reserve space - so over-committing does not fail safely, it fails
by filling the disk out from under whichever component grows last.

**Kafka is heavily over-provisioned and that is where the slack is.**
Measured 842 MB per broker at dev scale; scaled by the fleet ratio that is
roughly 22 GB against a 96 GB quota - a 4x margin. Cutting kafka to 64g
per broker would free 96 GB and take the whole commitment to 712 GB (80%),
which is the difference between tight and comfortable. Worth doing, but on
a rate-based measurement of its own rather than this rough scaling -
Kafka's quotas have never been measured, unlike ClickHouse's now.

**`cdc.drivers` is the single largest thing in Kafka**, at 289 MB across
three partitions against roughly 480 MB for all twelve `driver_location`
partitions together. The cause is architectural, not a bug:
driver-service persists last-known position with `UPDATE drivers SET
last_lat, last_lon, last_seen_at` every tick, and `nus.drivers` is in
Debezium's `table.include.list`, so every position write also becomes a
CDC message. Driver positions therefore travel the pipeline twice. The
compacted topic retains only the latest row per driver, which is what
cache-updater actually needs, so the cost is the intermediate updates
before compaction rather than the end state. Options if it matters at full
scale: persist position to PostgreSQL less often than every tick, or
accept the duplication. Not decided; measure at the next scale rung.

**PostgreSQL was not measured** - the `du` returned nothing, so the
Patroni image keeps its data somewhere other than
`/var/lib/postgresql/data`. Find the real path before trusting the 24 GB
quota.
- Partition `trips` by month on `requested_at` in Postgres, so the
  archiver drops partitions instead of deleting rows. Moved here from the
  old step 2 list: it is a capacity decision, not a schema-design one.
- Budget the F6 envelope's real cost on `driver_location` (~20,000 msg/s
  at full scale, so roughly 40-60 extra bytes on every one). This is a
  sizing input now, not a decision — the envelope is on all six topics
  either way, so what has to move if it does not fit is retention or
  partition count, not the envelope.
- Budget the `dispatch_offers` topic. It carries several messages per
  completed match, not one, so it sizes with offers-per-match rather
  than with trip volume. Measured 2.02 offers per match on Dionysus at
  dev scale, so budget roughly twice the trip volume, not equal to it.
- **city-service cannot keep up, and the gap is widening.** Measured 2,976
  messages of lag at 45 minutes and 11,080 at two hours, against 5.6M on
  `driver_location`, while every other consumer sat at 0-7. The visible
  consequence is already in the data: `hotspot_score` has a median of 0
  and mean surge is 1.02, so the surge half of the simulation is inert
  because city-service's demand picture is stale rather than because
  demand is low. It is the only consumer reading every position to score demand,
  and at full fleet that gap becomes the reason the demand picture is
  stale rather than merely late. Decide whether it samples positions
  rather than reading all of them, or runs as more than one instance -
  and measure before choosing.

**Test:** DIONYSUS — measure ingest rate and on-disk growth over a fixed
window; extrapolate.

**Done when:** the projection fits the quota with stated headroom, written
down in the ClickHouse README, and TTL drops partitions rather than rows.

---

## Step 7 — Data-collection quality bars
DONE — 2026-09-26, Dionysus. All **15 bars pass** against live traffic
(`make verify-quality`, exits 0). Every bar is structural, so the same
numbers hold at any scale.

The bars found three real defects that nothing else had surfaced, which is
the argument for having them:

- **Q5** — `rider_no_show` was reachable before the driver had arrived,
  because the pre-arrival reason list still contained it. One row in a few
  thousand.
- **Q8** — trips in `no_driver_found` carrying an accepted offer. Dispatch
  offered the ride before computing the route, so a routing failure left
  the contradiction behind. Two rows in six hours.
- **Q0** — added after finding that all fourteen other bars reported `ok`
  against a completely empty warehouse, since every one of them counts
  violations.


Today's bars prove rows exist and align. None score whether the generated
data is any *good*. Add numeric bars (ASSESSMENT §7 already has the frame):

- Null rate per nullable column, with a ceiling.
- **Uniqueness, and it is load-bearing.** `count() = uniqExact(event_id)`
  on every ClickHouse table, and zero rows whose `event_id` is null. This
  is the bar the whole F6 decision rests on: ClickHouse does not dedupe
  for us, so every aggregate in Grafana and Superset is only as
  trustworthy as this number. It fails loud or the warehouse is not
  trusted.
- Referential integrity: zero orphan rider/driver/trip references.
- Funnel integrity (F5): every `dispatch_offers` chain for one trip has
  contiguous `sequence` values starting at 1, at most one `accepted`, and
  a trip in `no_driver_found` has no accepted offer.
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

- Fulfillment rate (requests → completed) and cancellation rate by reason
  — both need F1's `cancellation_reason` in the warehouse and F7's
  `fulfilment_hourly`, since today every rollup only sees completed trips.
- Rider wait time — request → pickup (needs step 3's `arrived_at`).
- Take rate: platform fee vs driver payout vs gross (needs F1's
  `driver_payout` on the wire).
- The matching funnel (F5): acceptance rate, offers per match, and
  time-to-match by zone — the panels the whole `dispatch_offers` decision
  exists to make possible.
- Surge effectiveness: surge multiplier vs subsequent fulfillment in-zone,
  and whether surge measurably lifts acceptance rate.
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

**Fulfilment is 0.572 at 4,000 drivers, and raising supply did not fix
it.** Measured over two hours: 1,351 of 5,115 ended trips are
`no_driver_found` - 26%, slightly WORSE than the 23% at 800 drivers. Chain
exhaustion cannot explain it: at 0.533 acceptance over five offers, all
five refusing happens 2% of the time. The remaining ~24% is
`find_candidates` returning nothing at all, which at 2,400 online drivers
over roughly 1,200 km2 should not happen inside a 5 km radius. Something
about how drivers enter or leave the per-tier Redis GEO sets is the
suspect, not the supply ratio. Diagnose before building a fulfilment panel
on it, or step 9 charts a number nobody trusts.

**Scale supply and demand together, which the dev profile does not.**
Measured on Dionysus: 800 drivers against ~68 requests/minute is 11.8
drivers per request-per-minute, where `.env.example`'s full-scale numbers
(106,000 drivers, 455 requests/minute) give 233 - roughly twenty times
more supply per unit of demand. That gap, not a defect, is why the dev
profile reads 0.561 fulfilment with 23% `no_driver_found`: dispatch
genuinely cannot find a free driver within `DISPATCH_SEARCH_RADIUS_KM`.
Every intermediate rung has to hold that ratio, or each stage measures a
different marketplace and none of them predicts the last one.

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
