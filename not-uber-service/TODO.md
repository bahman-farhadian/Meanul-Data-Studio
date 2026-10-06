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

| Block | Steps | Where | Status | Why they combine |
| --- | --- | --- | --- | --- |
| A | 6 (measure) + 7 | Dionysus, one bring-up | **DONE** | Both read the same live stack: measure bytes/row while the quality bars run |
| B | 8 + 9 + 10 | Dionysus, one bring-up | **DONE 2026-10-02** | ksqlDB, Grafana and Superset all read the same warehouse tables - build all three, verify once |
| D1 | 12 | Dionysus, two bring-ups | started, not passed | The staged scale-up: ~10% of target fleet, then ~50%. The 2026-10-06 50% stack is wedged; it is not this row's pass |
| C | 11 | Local | after D1 | Contract and documentation. No server time |
| D2 | 13 | Dionysus, one bring-up | last | The full-scale final run |

**Re-ordered 2026-10-03: step 12 now runs BEFORE step 11.** The original
order was A, B, C, D with 11 sitting between B and D. Three reasons it
changed, and one reason step 13 did not move with it:

- **Step 12 produces the facts step 11 is supposed to write down.** The
  staged scale-up settles the disk projection, city-service lag at real
  volume, and the surge question - seed ratio or the 0.6 threshold.
  Writing the contract first means writing it twice.
- **Step 11 adds no verification strength, so nothing is lost by moving
  it.** Its deliverables are ASSESSMENT.md bars, a test that reads those
  bars back out of the sources, and a generated ERD. None is a new runtime
  check: `verify-ksqldb`, `verify-superset` and the fifteen quality bars
  already exist and already run. Step 11 documents instruments and guards
  the document against drift.
- **Step 12's own Done-when never mentions ASSESSMENT.md.** It is the
  verify suite, no OOM, lag recovering, disk within quota. All of those
  exist today.
- **But step 13's Done-when IS ASSESSMENT.md §9**, which is that file's
  definition of version 1 being closed. Run it before step 11 and the
  final test has no complete definition of done. So 13 stays after 11.

The original rationale is better served, not violated: it said the
contract should be written down *immediately before the full-scale run*,
and in this order step 11 still is - it has simply gained the step-12
measurements to write about. Step 11b also stops being a judgement call,
because the 50% stop measures what a week of idle telemetry really costs.

**Blocks A, B and C run at dev scale, and deliberately.** Correctness is
what they test, and correctness is scale-independent.

Four things are not scale-independent, and must never be "fixed" on the
strength of a dev-scale reading:

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

## Fixes before the next Dionysus run

Reviewed 2026-10-06 against two archives, not against memory. The crash
archive is `/tmp/nus-crash-readback.tgz`, host Dionysus, sha
`0a30b9f49b2b029937d55829813732316b654feb`, captured
`2026-10-06T09:54:51Z`. The earlier scale archive is
`/tmp/nus-50pct-readback.tgz`, same sha, captured `2026-10-06T09:10:38Z`.
A number below names which archive it came from. The 50% stack those
archives describe is not a passed step 12. Do not start the night
bring-up on the image that produced them.

The bring-up that is already written (destroy, init, up, etcd-existing)
stays the protocol. These items are what has to be in that image first.

### Must-fix before another bring-up

**1. Dispatch dies on an offer it already wrote, and the partition stays stuck.**

Symptom, crash archive. 129 tracebacks in `dispatch-tracebacks.txt`.
127 end in `psycopg.errors.UniqueViolation: duplicate key value violates
unique constraint "dispatch_offers_one_accepted_idx"`. One ends in
`psycopg.errors.SerializationFailure: canceling statement due to conflict
with recovery`. The file keeps a stack only for the last traceback, and
that stack is the `UniqueViolation` in `record_offers`. The dispatch
`SerializationFailure` has no stack in the extract, so its call site is
unknown. Item 1 does not cover it. It is not the same bug as the unique
index, and it is not dismissed. One extractor bucket is the info line
`"message": "stopped", "matched": 6` — that is `main` returning, not an
exception.
`signals.txt` says `dispatch stop signals: 0` and `dispatch unique
violations: 128`. `inspect-dispatch-service.txt` says `restarts=129`,
`oom=false`, `exit=0`, `started=2026-10-06T09:53:58Z`,
`finished=2026-10-06T09:53:57Z`. The captured Postgres tail on `nus-pg-3`
names the colliding rows `trp-20261005-SpN2rr5i`,
`trp-20261005-JRnUdI5p`, and `trp-20261006-lemnDpAF`, about 45 seconds
apart, all `Key (trip_id)=(...) already exists`. `pg-1` and `pg-2` had
no such lines in the snippet.

Code. `assign` calls `record_offers`, which commits the chain, and only
then `announce_offers` (`l-service-dispatch/dispatch_service/__main__.py`,
the call pair at `record_offers` then `announce_offers`). The partial
unique index `dispatch_offers_one_accepted_idx` allows one accepted offer
per trip (`h-bootstrap/migrations/015_dispatch_offers.sql`). The Kafka
offset is committed only after the whole poll batch
(`consumer.commit()` after the `while taken < max_per_tick` loop). A
death after the Postgres commit and before that offset commit leaves the
offer stored and the request unacked. The next process inserts a second
accept, the index rejects it, the exception is not caught, the process
dies, Docker restarts it (`restart: unless-stopped`), and the same
messages are read again.

`doubled-offers.txt` is the warehouse view of the same replay: 48 trips
with `accepted > 1` or a gapped chain. The worst are
`trp-20261005-saedE9qZ` (accepted=75, offers=77, top_sequence=3) and
`trp-20261005-WAEVOd8E` (accepted=75, offers=78, top_sequence=3), both
still receiving events at `2026-10-06 09:52:25`. Other batches share one
`first_event` and a round replay count (50, 25, 22, 5, 3). The 09:10
quality file already had Q6 = 42 and Q7 = 42. Q6 is
`countIf(accepted > 1)` over trips (`z-config/quality.sql`,
`two_accepted`), so a trip accepted 75 times counts as one failed trip,
the same as a trip accepted twice. Q6 does fail. It does not measure the
depth. `trip_facts` stays one row per trip, so a uniqueness check on
that table still passes while the offer topic is full of replays. The
night retest has to read `max(accepted)` per trip, not only the Q6 trip
count, and not only `trip_facts`.

Outcome. An offer insert that finds the accept already stored is treated
as that request already handled: no second publish, no process death,
and the offset for that message is committed. One poisoned trip cannot
pin the head of a partition.

Retest. After a clean cycle, `docker inspect dispatch-service` stays at
`restarts=0` for the whole run. The dispatch log has zero
`dispatch_offers_one_accepted_idx` lines. Q6 and Q7 are 0, and
`max(accepted)` grouped by `trip_id` in `nus.dispatch_offers` is 1.
`make lag` shows `dispatch-service` on `trip_requests` moving, not stuck
in the tens of thousands.

**2. The match clock is taken before the request exists.**

Symptom, 09:10 archive only (`reports/verify-quality.txt`). Q3
`no negative milestone lag` measured 24, pass line `negative = 0`. Q4
through Q5 and Q8 through Q14 were 0. The crash archive does not repeat
this query. The 24 is not a parent-chat recollection.

Code. The dispatch loop takes `now = utc_now()` once per tick and passes
that value into every `assign` in the tick. `matched_at` is set to that
`now`, and `routing.route` for the pickup runs before the stamp. A
`pgr_ksp` call in an earlier request of the same tick takes seconds.
Passenger keeps publishing, the next `poll_once` in the same tick
returns those new requests, and their `requested_at` is later than the
frozen `now`. `match_s` is `dateDiff` of those two columns and comes out
negative. The other milestone columns on those rows are not the bug.

Outcome. `matched_at` is the clock after the route returns, not the
clock from the start of the tick.

Retest. `make verify-quality` reports Q3 measured 0 on the new run.
A query of `min(match_s)` on `nus.trip_facts` is not negative.

**3. A replica cancel in `nearest_road_point` kills driver-service.**

Symptom, crash archive. `driver-tracebacks.txt` has one traceback. It
ends in `psycopg.errors.SerializationFailure: canceling statement due to
conflict with recovery`, raised from `_follow_jobs` → `drive_path` →
`route` → `nearest_road_point` → `postgres.fetch_one`.
`inspect-driver-service.txt` says `restarts=7`, `oom=false`, `exit=0`,
`started=2026-10-06T08:30:23Z`. The log explains one of those seven
exits. The other six are not in the captured traceback extract; do not
invent a cause for them. Both inspect lines also say `exit=0` next to an
uncaught exception. That disagreement is in the files. The retest
records the exit code rather than this note explaining it.

The 09:10 log slice is a different fact about the same query family.
`pgr_ksp` retries were 441 in driver-service and 91 in dispatch, and
`gave up` was 0 for both. Those retries already work. They are not this
fix. The captured stack is `_follow_jobs` → `drive_path` → `route`
(`nus_common/routing.py`, the snap at the start of `route`) →
`nearest_road_point` → `postgres.fetch_one`. `route` calls
`nearest_road_point` on both ends before `_query_with_retry`. The retry
wraps only the `pgr_ksp` query. A replica cancel on the snap therefore
escapes it and kills the process. The fix belongs on
`nearest_road_point` as `route` calls it, not on a call that sits in
front of `route`. The 09:10 archiver slice shows why the replica was
cancelling queries: the first prune ticks deleted 100000 rows with
`hit_ceiling: true` (8.42s, then 8.81s, then 8.32s). That is the
`conflict with recovery` load. It is not a reason to wrap `pgr_ksp`
again.

The same restart also stops positions. `driver_service/__main__.py`
creates the Kafka producer only after the initial `_follow_jobs` (the
pool that builds a street path for every driver brought online at
start). Until that pass returns, a restarted process publishes nothing.
The 09:10 profile shows it: `driver_positions` newest is
`2026-10-06 08:13:49.780`, `behind_min=58`, while the crash inspect says
driver-service had been up since `08:30:23Z`. The log slice's last tick
is `08:14:23`, which is the process from before that restart. ClickHouse
sink lag on `driver_location` was 0 on every partition in the 09:10 lag
file, because the topic had stopped growing, not because the sink was
behind. A restart at this fleet size is a position stall, not a 40-second
tick.

Outcome. `nearest_road_point`, as called from `route` before
`_query_with_retry`, survives a `SerializationFailure`: retry, then fail
that one lookup, and do not exit the process. A restart must not sit
silent for the whole initial path pass; positions are published while
that pass runs, or the pass does not stand between process start and the
producer.

Retest. While the archiver is catching up a multi-day seed,
`driver-service` stays at `restarts=0`. A `conflict with recovery` on
the snap inside `route` is a retry line, not a traceback. If the process
does restart, `driver_positions` newest moves during the initial path
pass instead of freezing at the last tick of the previous process.

### Code-only, not a captured defect

Neither archive shows a bootstrap failure. Services were already up
(`passenger-service` started `2026-10-05T20:06:41Z`). There is no
bootstrap log, because `make bootstrap` uses `docker compose run --rm`.
The missing run log is a gap. It is not a must-fix defect.

The source order is still worth knowing before someone splits history
off the live start, and only as code. `h-bootstrap/bootstrap/__main__.py`
sets `system:bootstrap:done` only after `history.generate`, the Postgres
store, and the ClickHouse load. `history.py` walks
`day_offset` from `history_days` down to 1, so a two-day seed emits
offset 2 and offset 1. `warehouse.already_loaded` skips that load once
any `trip_events` row exists. Moving the marker earlier without changing
that guard would drop the week on the floor, and inserting the old days
into Postgres after the archiver is already running would delete them
and stream them onto the live bus. No retest bar is attached here,
because this run did not fail bootstrap.

### Observed, not a retest blocker

These are real readings. Leaving them as they are does not reproduce
the dispatch wedge. The night run should record them again. It should
not be held for a redesign of them.

- **Archiver catch-up hits the ceiling, then finishes.** 09:10 log
  slice: the first ticks deleted 100000 rows in 8.42s, 8.81s, and 8.32s
  with `hit_ceiling: true` (`max_batches_per_tick` 20, `batch_size`
  5000). Later ticks fell under the ceiling. The last captured tick is
  `this_tick: 2, total: 655000, seconds: 0.21, hit_ceiling: false`.
  655000 is `2 × HISTORY_TRIPS_PER_DAY` (327500), the whole
  `HISTORY_DAYS=2` seed. `history.py` emits `day_offset` 2 and then 1.
  By the `05:06Z` tick both of those days are older than the 24h
  retention, so the archiver removed both seed days, not one extra day.
  Steady state after that kept up. A 7-day seed will
  spend longer on the ceiling during catch-up. That is not, by itself,
  a reason to partition `trips` before the night run. `inspect` shows
  `archiver-service` `restarts=0` since `20:06:41Z`.
- **Before 08:14 the tick was slow. After the 08:30 restart it stalled.**
  `meta.txt` in the 09:10 archive has `DRIVER_TICK_SECONDS=3.0`. The log
  slice has 636 `"message": "tick"` lines, and the last of them is
  `08:14:23`. Spacing in that slice is about 37–63 seconds (first gaps
  `20:52:01` → `20:52:47` → `20:53:50`; last gaps about 37s) with
  `path_p50` rising from 22 to 133. That spacing is not the state at
  09:10. The 09:10 profile shows `driver_positions` newest
  `2026-10-06 08:13:49.780`, `behind_min=58`. The crash inspect says
  driver-service had been up since `08:30:23Z`. ClickHouse sink lag on
  `driver_location` was 0 on every partition, so the sink was caught up
  to a topic that had stopped. The cause is item 3: the producer is
  created only after the initial `_follow_jobs`, so a restart publishes
  nothing until that pass finishes. The 09:10 position check (4977
  trips, off-network 0, route 6.32 km against a chord of 4.65 km) is
  trips that ended before this stall. The capacity verdict of 3.16 GiB
  per node, 2.8% of 112 GiB, counts rows written before the stall. Do
  not read either number as a healthy 40-second tick.
- **City-service and the sink were behind, once.** 09:10 profile:
  `city-service` `partitions=31 total_lag=4309`, `clickhouse-sink`
  `partitions=49 total_lag=2806`. One sample each. Neither process had
  restarted by 09:54 (`restarts=0`, still the `20:06` start). Step 12
  already says one city-lag sample is not a shape. Do not add a second
  copy of either service on this snapshot.
- **`pgr_ksp` retry already absorbs the replica cancel.** 441 driver
  retries, 91 dispatch retries, 0 give-ups, in the 09:10 slice. Leave
  that path alone. Item 3 is the call that does not use it.
- **Dispatch lag at 09:10 was the wedge, not a separate consumer bug.**
  `dispatch-service partitions=7 total_lag=66111`. That is item 1.
  `cache-updater` was `total_lag=2`. `passenger-service` and
  `driver-service` group lag were 0.

### Not judgeable from these logs

No defect is claimed here. No clean bill either. The crash archive has
no log body for them, and the 09:10 archive has only container health
unless noted.

- **Grafana.** `nus-grafana` healthy, restarts 0, in the 09:10 health
  file. No panel result and no grafana log in either archive.
- **Superset.** `nus-superset` healthy, restarts 0. No chart result and
  no superset log in either archive.
- **Debezium.** `nus-debezium-connect` healthy, restarts 0. Connector
  task state is not in either archive. Downstream, the 09:10 profile
  showed Redis db 1 = 106003 and db 2 = 750000, and cache-updater lag
  2, so a snapshot had been applied by then. That is not a connector
  log.
- **Passenger-service application log.** Inspect: `status=running`,
  `restarts=0`, `started=2026-10-05T20:06:41Z`, `oom=false`. It did not
  exit. Its log was not collected, so a handled error is invisible.
- **Kafka, schema registry, and ksqlDB error text.** All three Kafka
  brokers, `nus-schema-registry`, and `nus-ksqldb-server` were healthy
  with restarts 0. The 09:10 lag command listed topics and the consumer
  groups above, so the broker answered. No broker or ksqlDB error line
  was archived.
- **Bootstrap run log.** Absent, because the one-shot container is
  removed. Neither archive shows a bootstrap failure. The source note
  under "Code-only, not a captured defect" is not this gap and is not a
  must-fix.

`/tmp/nus-collect-component-logs.sh` collects those six. It does not
print secrets. It is not part of the repo.

### Component index

| Component | Class |
| --- | --- |
| Postgres/Patroni | Reviewed-clean as a cluster. 09:10 health: `nus-pg-1/2/3` and `nus-etcd-1/2/3` healthy, restarts 0. The only SQL errors in the crash snippet are `dispatch_offers_one_accepted_idx` on `nus-pg-3`, which is item 1, the index doing what it was added to do |
| Redis/Sentinel | Reviewed-clean. 09:10 health: three redis and three sentinel, healthy, restarts 0. Profile key counts: db 0 = 1 (bootstrap flag), db 1 = 106003, db 2 = 750000, db 3 = 141230 |
| Kafka, schema registry, ksqlDB | Log gap for error text. Process health and the lag listing are under "Not judgeable" |
| Debezium | Log gap for connector state. Container health is under "Not judgeable" |
| ClickHouse | Reviewed-clean. Four servers and three keepers healthy, restarts 0. The 09:10 capacity and profile queries returned. The 3.16 GiB/node figure is the pre-stall reading in the observed list, not a cluster fault |
| Grafana | Log gap |
| Superset | Log gap |
| HAProxy | Reviewed-clean. `nus-lb-a` and `nus-lb-b` healthy, restarts 0. The 09:10 quality, position, capacity, and profile commands all reached Postgres or ClickHouse through the published ports |
| bootstrap | Log gap. No run log and no captured failure. The marker and `already_loaded` notes are code-only, not a defect in this run |
| cache-updater | Reviewed-clean. Crash inspect `restarts=0` since `20:06:41Z`. 09:10 lag `total_lag=2` |
| driver-service | Must-fix item 3. The snap inside `route` killed one process, and the 08:30 restart then published no positions through the 09:10 profile |
| passenger-service | Log gap for the application log. Inspect shows it never exited |
| dispatch-service | Must-fix items 1 and 2. This is what is stopping the stack |
| city-service | Reviewed-clean as a process: crash inspect `restarts=0` since `20:06:42Z`. One 09:10 lag sample, 4309, is not a retest blocker |
| clickhouse-sink | Reviewed-clean as a process: crash inspect `restarts=0` since `20:06:41Z`. One 09:10 lag sample, 2806, is not a retest blocker. `driver_location` lag was 0 because the topic had stopped, which is the driver stall in item 3 |
| archiver | Reviewed-clean as a process: crash inspect `restarts=0` since `20:06:41Z`. Catch-up hit the ceiling, then removed both seed days (total 655000). Not a retest blocker |

---

## Block B closed — 2026-10-02, measured on one clean bring-up

`make destroy` + `init` + `up` + `etcd-existing`, then five hours of live
traffic before the evaluation ran. Everything below came off that one
stack, not from separate runs.

| What | Measured | Bar |
| --- | --- | --- |
| Quality bars | **15 of 15 pass** | all structural, scale-independent |
| ClickHouse | 46 tables, `absolute_delay` **0** on all 17 local | at or near 0 |
| ksqlDB | **7 streams + 1 table**, query RUNNING, 3 client settings set | every declared object |
| Grafana | **46 panels** all returned rows | every provisioned panel runs |
| Superset | **20 charts** match files and return rows, 6 datasets | declared = held, every metric runs |
| Uniqueness | `driver_positions` 11,739,735 rows = 11,739,735 event ids, 0 blank | exact |
| Capacity | **69.28 GiB/node of 112 GiB = 61.9%**, verdict ok | 30% headroom |
| Fulfilment | **0.772** (7,034 completed of 9,106 ended) | not a bar - see step 12 |
| `no_driver_found` | **417 of 9,106 = 4.6%**, down from 26% | the tier fix holding |
| Fleet tiers | `economy 2817, xl 768, premium 415` | 70/20/10 as seeded |
| Money | take rate 0.23, summed as `Decimal(38,2)` | exact, no float tail |

### What the run found, and what was done about it

- **`make verify-walk` and `make verify-sample` died with `uv: command not
  found`.** The only two targets here that run pytest directly rather than
  driving Docker, and nothing checked the tool existed. Dionysus
  deliberately has no `uv`. Guarded with a message naming them as
  workstation targets - NOT added to `preflight`, which runs inside
  `make up` on the host and would then fail a bring-up over an unused
  tool.
- **`verify-data`'s trip-count hint contradicted the archiver.** It said
  trips should be roughly HISTORY_DAYS x HISTORY_TRIPS_PER_DAY. Measured:
  PostgreSQL held 8,199 with the oldest `ended_at` 24.2 hours old against
  `ARCHIVER_RETENTION_HOURS=24`, while `trip_events` held 9,457. Postgres
  is the operating window, the warehouse is the history, and the hint
  read a working archiver as missing data.
- **Step 11c blamed the wrong thing for flat surge.** Corrected in place -
  it is the seed ratio and the 0.6 threshold, not consumer lag.
- **Part D of the earlier plan round was formally dropped.** Recorded
  below under Closed by decision.
- **Superset's partial-data notice is gone for good.** The OD chart was a
  top-100 table that warned on every load. It was tried as a heatmap -
  5,614 filled cells in a 69,169-cell grid at this seed, 92% empty, 3px a
  cell, unreadable - and reverted to a paginated table carrying a row
  limit of 69,170. That clears the 69,169 pairs 263 TLC zones allow, so
  the rows returned can never reach the limit at any scale.
- **`verify-assets` was proving less than it claimed.** It rebuilds each
  chart's query from groupby, x_axis, the window and row_limit and nothing
  else, so it reported 6,035 rows for a chart carrying `series_limit=25` -
  the unnarrowed count. It now refuses any chart setting a narrowing
  parameter it does not apply, because a query modelled wrongly is worse
  than one skipped. **Still unmodelled: `ORDER BY`.** The sampled row is
  therefore not the top row, and the sort is checked statically instead.
  Worth closing in step 11.

### Noise confirmed as noise

- **ClickHouse code 210 on all four nodes**, 27 times in five hours.
  `172.18.0.27:8123 -> 172.18.0.15`, logged by `StaticRequestHandler`,
  which is what serves `/ping` - and `/ping` is what HAProxy health-checks
  (`haproxy.cfg.template:218`). Health-check resets, roughly 0.3% of
  checks.
- **Consumer lag oscillates, it does not grow.** Two samples ten minutes
  apart moved in opposite directions: city-service 2,208 -> 14,921,
  clickhouse-sink 5,051 -> 1,168. Both batch, so lag spikes after a
  produce burst and drains. A single lag sample proves nothing about
  either, which is why the earlier "4x increase" reading was withdrawn.

### Open decisions carried into step 12

| Decision | Blocks | Where it is written |
| --- | --- | --- |
| Surge: seed ratio, or the 0.6 threshold | step 12 | step 11c |
| city-service lag: CPU, instances, or score on a sample | step 12 | step 11c |
| Kafka quota, 96 GB/broker against ~842 MB measured | step 12 | step 6 |
| Seed idle telemetry, or label the panels | step 13 | step 11b |

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

**The real gaps were these.** Step 2 is done and `docs/schema-review.md`
argues each against a real source; the finding ids point into it. Status
reviewed 2026-10-03 against the shipped files - **eleven of thirteen are
closed, two are not**, and the two are called out below the table because
neither had its status written down anywhere.

| Gap | Status | Why it mattered |
| --- | --- | --- |
| Four `trips` columns never leave Postgres (F1) | **closed** steps 4–5 | `driver_payout`, `payment_method`, `cancellation_reason`, `requested_vehicle_type` are in no `.avsc` and no ClickHouse DDL — so take rate, payment mix, why trips cancel, and anything per tier are unanswerable in the only store Superset may read |
| Money is `Float64` in ClickHouse (F3a) | **closed** step 3 | Float addition is not associative and SummingMergeTree sums `revenue` during background merges in an uncontrolled order, so the total depends on merge history — measured at ~1e-9 relative, so sub-cent, but it never reconciles against Postgres's `numeric(10,2)` and carries a float tail onto every dashboard |
| No `event_id` on any event (F6) | **closed** step 4 | A replayed sink batch is indistinguishable from a genuine repeated status, and `trip_events_local` is a plain ReplicatedMergeTree that will not dedupe it. Also fails our own §3.3 correlation-id rule |
| Every rollup filters `completed` (F7) | **closed** step 4 | Cancellations and unmatched requests appear in no aggregate we produce — fulfilment rate, the most basic health metric, needs a self-join over a year of raw events |
| No driver-arrival timestamp (F2) | **closed** `013_arrived.sql` | Rider wait time and post-arrival cancellation — two core ride-hail metrics — are not computable from the data at all |
| A driver can never decline (F5) | **closed** `015_dispatch_offers.sql` | Dispatch assigns directly, so acceptance rate, offers per match and time-to-match do not exist. Real platforms offer with a deadline and re-offer on decline. **Decided 2026-09-25: in scope, in full** |
| Payments are two columns on `trips` (F4) | **OPEN** — see below | Real platforms model payment as an append-only record (method, amount, status, refunds, adjustments); ours cannot express a failed or refunded charge, and overwriting the column would destroy the history |
| `trips` is one unpartitioned table | **OPEN** — see below | At 7 days x 655k/day the archiver deletes by row instead of dropping a partition — bloat and vacuum pressure at exactly the scale we intend to prove |
| `driver_positions`: monthly partition, 3-day TTL | **closed** step 6 | TTL deletes inside parts rather than dropping partitions; at full-fleet tick rate this is the heaviest table in the stack |
| ksqlDB is empty | **closed** step 8 | Deployed and authenticated, zero streams registered — DBeaver connects and correctly shows nothing. The only bar today is "server RUNNING" |
| Superset has no dashboards | **closed** step 10 | `g-infra-superset/init/` registers connections only. The analytical-BI tier is an empty shell |
| No marketplace KPIs | **closed** step 9 | Fulfillment rate, cancellation rate, rider wait time, take rate, surge effectiveness — none are panels today |
| Generation quality is unscored | **closed** step 7 | Bars prove rows exist and align; nothing scores distributions, null rates, or fidelity to the TLC calibration |

### The two that are still open

**F4 — payments are still two columns on `trips`.** `011_payout_and_payment.sql`
added `driver_payout` and `payment_method`, which is exactly the shape the
gap called insufficient: a failed or refunded charge cannot be expressed,
and overwriting the column destroys the history. This is not an oversight -
step 3 put `payments` and `driver_earnings` in **Tier 2, "if steps 3-5 come
in under budget"**, and of Tier 2 only `dispatch_offers` was built. But the
tier was never closed out, so the gap has sat here looking open with no
decision beside it. **Decide in step 11**: build it, or move it to
FUTURE_ROADMAP with the reason. It is not a scale risk either way.

**`trips` is still one unpartitioned table, and this one IS a scale risk.**
No migration carries a `PARTITION BY`, and no step in this file records a
decision about it. The gap's own words: at 7 days x 655k/day the archiver
deletes by row rather than dropping a partition - bloat and vacuum pressure
at exactly the scale step 13 intends to prove. `driver_positions` got
daily partitions in step 6 for precisely this reason; `trips` did not.

At the dev seed it is invisible: the archiver prunes ~50 rows a tick and
PostgreSQL sits at 128 MB. At full scale it prunes 655,000 rows a day from
a table holding 4.6M. **Watch it at both step 12 stops** - prune duration
per tick and table bloat - and decide there whether `trips` needs range
partitioning on `ended_at` before step 13 runs.

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
DONE — closed 2026-09-27 at **58.5% of quota**, measured on a clean
bring-up. The history of how it got there follows, because the first
answer was wrong.

Reopened 2026-09-27: `make capacity` read **80.22 GiB per node
against the 112 GB quota, 71.6%, verdict FAIL** - past the 30% headroom
this step set. Nothing regressed: the driver-tier fix raised fulfilment
from 0.63 to 0.70, more trips run, and `rider_positions` is only written
while a rider is ON a trip. Its measured rate went 14.7 -> 18.4 -> 46.7
rows/s across three readings and has not settled.

**RESOLVED 2026-09-27, and not by retention alone.** Cutting a TTL does
not reduce what is stored - it deletes what was answered. The right shape
is a short life for the raw rows and a long one for the answer, which is
what 012_position_rollups.sql adds:

- `driver_activity_hourly` and `rider_activity_hourly`, keyed on
  (hour, zone_id), AggregatingMergeTree, no TTL.
- Both position tables now expire in **2 days**; rider_positions was 7.

The property that matters is that the rollups are bounded by the CITY, not
by the fleet: 256 zones x 24 hours is **6,144 rows a day whether there are
4,000 drivers or 106,000**. The raw stream is bounded by every device
ticking; the rollup is not.

Measured on a real ClickHouse built from this DDL: **50,000 driver
positions collapsed to 9 rollup rows**, with `sum(positions)` exactly
50,000 and `uniqMerge(drivers)` exactly 400 against a true 400. The rider
side: 30,000 rows to 9, riders and trips both exact.

Projection from the measured rates:

| | per node | of 112 GB |
| --- | --- | --- |
| before | 80.21 GiB | 71.6% FAIL |
| rider_positions 7 -> 2 days | 70.88 GiB | 63.3% |
| plus both rollups, a year of them | 71.01 GiB | 63.4% projected |
| **measured on Dionysus, 2026-09-27** | **65.52 GiB** | **58.5% ok** |

The rollups cost 128 MiB a year and buy back history that used to stop at
the TTL: fleet presence, distinct drivers, speed and status mix per zone
per hour, kept indefinitely, where before there was nothing past two days.

`driver_positions` is now the only lever left worth pulling - it is 57 of
the 71 GiB. Its rollup exists now, so cutting it to one day is a decision
about the live map rather than about losing history. Not taken here.

---

Previously: DONE — 2026-09-26, Dionysus. `make capacity` reads **63.55 GiB per node
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
DONE — re-confirmed 2026-09-27 on a clean bring-up. All **15 bars pass**
(`make verify-quality`, exits 0).

**Two of the bars were themselves wrong, and the clean run is what found
them.** Both compared two tables by reading them separately, and ClickHouse
reads each table at its own moment - so on a live stream each bar reported
whatever arrived in between:

- **Q14** read 1. Dispatch writes the offer BEFORE the trip's first
  lifecycle row; `trip_events` has no `requested` status, so a trip's
  earliest row is `matched` or `no_driver_found` and both come only after
  the chain resolves. Every trip being dispatched at that instant had
  offers and no trip row - correctly.
- **Q13** read 3. It subtracted two independently-timed counts, which
  could also have read zero while trips were BOTH missing and duplicated,
  since the two errors cancel. Now an anti-join.

Both carry a five-minute settle window. The process failure was fixing
Q14 and shipping it without asking which other bars had the same shape,
so it is a rule now: `test_assessment_standard.py` parses every CTE in
quality.sql and requires a settle window on any bar touching two tables,
with a short exemption list that states why each one cannot need it. The
rule was verified against the OLD Q13 text - it catches it.

The bars found three real defects in the pipeline as well, which is the
argument for having them:

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
DONE — 2026-09-26, Dionysus. `make verify-ksqldb` exits 0: **7 of 7 streams
and 1 of 1 table registered, the persistent query RUNNING**. The sink topic
came out `ReplicationFactor: 3, min.insync.replicas=2`, so the explicit
`REPLICAS = 3` carried through on a real cluster. `ksql_offer_funnel_by_zone_1m`
held 910 messages at the one-hour mark, so the table is computing rather
than merely existing.

**One failure on the way, and it was a testing failure.** The first attempt
aborted `make up` at `ksql-ddl` with "Schema for message values on topic
'driver_location' does not exist in the Schema Registry" - nothing after it
ran. The streams infer their value columns from the Registry, the Registry
is filled by the producers, and the producers start at bootstrap, three
steps later. It had been verified against a Registry filled by hand, which
is not the order a real bring-up has. `schema-init` now registers every
`.avsc` as `<topic>-value` straight after the topics are created.


`c-infra-kafka/ksql/` holds seven `CREATE STREAM` statements, one per topic
in `topics.tsv` (the six originally listed plus `dispatch_offers`), and one
materialized `TABLE`. Applied by `ksql-init`, a one-shot in the same shape
as `ch-ddl-init`, wired into `make up` after `topics`.

**Verified locally against a throwaway single-broker stack on the pinned
image** (`confluentinc/cp-ksqldb-server:8.3.1`), with the real `.avsc`
files registered in a real Schema Registry and real Avro messages produced
by `kafka-avro-console-producer`:

- A clean apply registers all eight objects; a re-apply reports all eight
  "already there" and changes nothing.
- Value columns are inferred from Schema Registry - only the KEY column is
  declared. Avro enum becomes `STRING`, `timestamp-millis` becomes
  `TIMESTAMP`, and `bytes/decimal(10,2)` becomes `DECIMAL(10, 2)`, so the
  money fields carry the same representation here as in Postgres, on the
  wire and in ClickHouse. `26.75 - 20.60 = 6.15` came back exact.
- `SELECT ... EMIT CHANGES` returned the four produced offers in order.
- A pull query on `OFFER_FUNNEL_BY_ZONE_1M` returned the aggregated window.
- `check-ksql.py` passes on that stack and exits 1 naming both objects
  after a stream and the table are dropped.

Two findings worth keeping:

- `COUNT_IF` does not exist in this ksqlDB version; `SUM(CASE WHEN ...)` is
  the portable spelling.
- ksqlDB does **not** inherit the broker's `default.replication.factor` for
  a CTAS sink topic. Left to the default it came out with one replica; the
  statement now says `REPLICAS = 3` and the broker refuses it outright when
  three brokers are not there.
- `INSERT INTO` these streams does not work, correctly: ksqlDB would
  serialize with a schema derived from the column types, which is not the
  schema the producers registered, and Schema Registry rejects it. They are
  read-only views of topics the services own.

**Test:** DIONYSUS — `make verify-ksqldb`, then DBeaver.

**Done when:** `SHOW STREAMS` lists all seven and `SHOW TABLES` lists the
funnel table, `make verify-ksqldb` exits 0, a `SELECT ... EMIT CHANGES`
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

DONE — 2026-09-26, Dionysus. `check-dashboards.py` passes on all six
dashboards, and `make verify-grafana` runs **all 46 panel queries against
the live warehouse**: every one valid, every rollup panel returning rows.

`nus-marketplace` (14 panels) covers all six: fulfilment and cancellation
rate, cancellation reasons, rider wait and time to match, take rate, the
matching funnel (acceptance rate, offers per match, ETA offered against ETA
accepted), surge against acceptance by band, duration error p50/p90, and
the zone table that says where to act. Every panel carries a description
saying what the number means.

Two rules are now enforced by `check-dashboards.py` rather than followed by
habit, across **every** dashboard:

- Every rate is `sum(x) / sum(y)`. The sources are SummingMergeTree, where
  a row is a partial sum until a merge that may not have run, so an average
  over rows averages an arbitrary grouping and drifts with merge history.
- No chart carries two units, and every charting panel carries one. Fourteen
  panels had no unit at all - a utilization drawn as 0.42 rather than 42%,
  a p95 as 900 rather than 15 min, revenue as 12045 rather than $12,045 -
  and three carried two scales on one axis. Split into their own charts.
- No two panels may claim the same square of the grid. Grafana reflows an
  overlap silently instead of refusing it.

**Test:** LOCAL `check-dashboards.py`; DIONYSUS `make grafana-check` + eyes.

**Done when:** the check passes and every new panel renders non-empty
against live data.

---

## Step 10 — Superset: the analytical BI tier
DONE — 2026-09-26, Dionysus. Imported onto a Superset that had never seen
them: **6 of 6 datasets, 20 of 20 charts, 1 of 1 dashboard**, and all 36
metrics executed against the live warehouse. Measured through them:
acceptance 1,780/3,281, fulfilment 1,291/2,239, gross 42,366.97 against a
payout of 32,622.58.


`g-infra-superset/render_assets.py` writes six datasets over the warehouse
rollups, twenty charts and one dashboard; `init-superset.sh` imports them
with `superset import-directory --overwrite`, before `register_database.py`
and never after - the bundle's uri carries no password, `--overwrite`
applies it, and only registering afterwards puts the real credential back.

The metrics live on the dataset, so a chart is built by picking "Fulfilment
rate" from a list and there is no path through the UI that produces an
average of averages.

**Verified locally on a cold Superset from the pinned image**
(`apache/superset:6.1.0`, SQLite metadata, no hand-clicking): 1 connection,
6 datasets, 36 metrics, 20 charts, 1 published dashboard, all twenty
attached to it.

**Every metric expression was then executed against a real ClickHouse** with
the six rollups created from this repo's own DDL. That found a defect that
would otherwise have reached the browser: a metric named after the column it
sums breaks ClickHouse, because the SELECT alias wins over the table column
and the next metric's `sum(revenue)` becomes a sum of an aggregate -
ILLEGAL_AGGREGATION, code 184, and only when two such metrics are selected
together, which is exactly what a dashboard does. Six metrics had it; three
of the six datasets would not have answered a single query. Renamed, and
`check-assets.py` now refuses the collision.

`verify-superset` runs the same metric execution against the live warehouse,
so `make verify-dash` now fails on a broken metric instead of passing on a
200 from `/health`.

**Test:** DIONYSUS — import onto a *fresh* Superset, then `make verify-dash`.

**Done when:** assets import clean from a cold start and every chart
returns rows.

---

## Step 11 — Contract and documentation

NOT STARTED. **Runs after step 12, not before** - see the re-ordering note
under Working blocks. Everything below is unchanged except its place in
the queue; the step-12 measurements are inputs to it rather than
something it has to predict.

- ASSESSMENT.md: bars for ksqlDB streams, Superset assets, the step-7
  quality numbers, and any new closed set — each with instrument, pass,
  fail.
- Extend `test_assessment_standard.py` to read them from the sources.
- Schema documentation: ERD plus a per-table, per-column data dictionary
  for both stores, generated from the live schema so it cannot rot.

**Test:** LOCAL — `make verify-walk`.

**Done when:** pytest exits 0 and the docs match what is actually shipped.

---

## Step 11b — The seeded history has no idle telemetry

Found 2026-09-27 while checking the dashboards. `h-bootstrap` writes
`driver_positions` only along trip paths and only with status `on_trip` -
there is no idle or en_route_pickup telemetry anywhere in the seeded week.

So every metric whose denominator is "time online" reads 100% across the
whole historical window and then falls to the real figure (~9%) the moment
live traffic starts. `driver_utilization_hourly` is the one that shows it,
on three panels: Grafana nus-history 6, nus-driver 5, and Superset's
"Driver utilization". The charts are honest; the history has no
denominator.

Not a structural defect, which is why no step-7 bar catches it - every row
is valid, unique and self-consistent. It is a GENERATION-quality gap, and
it belongs with the calibration work: the seeded week should look like the
live system, and on this measure it does not.

Two ways out, neither free:

- Seed idle positions too. Faithful, and `driver_positions` is already the
  heaviest table in the stack by an order of magnitude - a week of idle
  telemetry for the whole fleet is the single most expensive thing this
  project could choose to store.
- Leave it and say so on the panels, so nobody reads the cliff as a
  collapse in fleet efficiency.

**Decide before step 13**, because the full-scale run is where a week of
idle telemetry would actually hurt.

---

## Step 11c — Measured 2026-09-27, three answers

**PostgreSQL is 128 MB against its 24 GB quota.** The biggest tables are
the road graph - `ways` 40 MB, `ways_vertices_pgr` 22 MB - and the graph
does not grow with the fleet. **This does not settle the quota.** Unlike
Kafka, whose retention is time-bounded, the OLTP tables scale with drivers
and with trips: 4,000 drivers and ~1,200 trips today against 106,000 and
655,000/day at full scale. The measurement is recorded; the projection is
step 13's, and cutting the quota on a dev-scale reading is exactly what
this file's own rule forbids.

Two notes on the query that produced it: it was run on a REPLICA, so
`pg_stat_user_tables` read zero everywhere (those counters are per node),
and it filtered `nspname = 'public'`, which is why only four tables came
back. `pg_database_size` covers every schema, so the 128 MB total stands.

**city-service is behind on `driver_location` and on nothing else.**
Per-partition lag, all twelve partitions: 780-975. `rider_location` 20-58,
`trip_lifecycle` 4-9. Uniform, not one stuck partition.

The comparison that names the cause: **clickhouse-sink reads the same
topic with 66-97 lag per partition** - ten times less, same messages, same
broker. So the topic is not too fast; city-service's per-message work is
too heavy. It does a zone lookup and a speed sample per position where the
sink batches and inserts.

The lag is real and still belongs with the scale-up, because the fix is a
choice: more CPU for city-service, more instances, or scoring on a sample
of `driver_location` rather than every message. An aggregate score over a
sample is statistically sound and is the only one of the three that also
works at 18,000 positions a second.

**Corrected 2026-10-02: the lag is NOT why surge is flat.** This step
originally blamed the stale demand picture for `hotspot_score` sitting at
a median of 0 and surge at a mean near 1.0. The code says otherwise.
`city_service/counters.py` scores a zone `waiting / (waiting + free + 1)`
and `surge_from_score` returns exactly 1.0 for any score at or below 0.6.
Rearranged, surge only moves when **`waiting > 1.5 x free + 1.5`** - a
zone needs half again as many riders waiting as it has drivers free.

At 4,000 drivers over 256 zones that is about 15 free per zone, so a zone
needs ~24 riders waiting at the same instant. The measured live request
rate is ~26 per minute for the whole city. It essentially never happens,
and a perfectly current city-service would publish 1.0 just the same.
The median score is 0 for the same reason plus a deliberate one: the
function returns 0.0 outright when nothing is waiting, which is most
zones at most instants.

Two consequences, both for step 12:

- Lag and flat surge are separate problems. Buying city-service more CPU
  would not move surge by itself. Measured 2026-10-02 over two samples
  ten minutes apart, per-partition lag swings 2,208 -> 14,921 on
  city-service and 5,051 -> 1,168 on clickhouse-sink - it oscillates with
  the produce bursts in both directions rather than growing, which also
  means a single lag sample proves nothing about either.
- **Full scale makes this worse, not better.** Measured now: 4,000
  drivers against 25.8 requests/minute is ~155 drivers per
  request-per-minute. Step 13's seed is 106,000 against 455, which is
  ~233 - more oversupplied still. So "Does surge lift acceptance" stays a
  one-band chart at full scale unless the seed ratio or the 0.6 threshold
  is revisited. Deciding which is step 12's, not this step's.

**Superset's headline tiles were wrong, and it was mine.** `time_range:
"Last week"` resolves to 00:00 seven days back -> **00:00 TODAY**, so
every daily-grain chart dropped the current day. Revenue and Completed
trips read the seeded day exactly - 1,166 and $28,473.37 against a true
3,297 and $66,099.68. Fixed with an explicit window that reaches `now`,
and the test refuses any of the friendly names that end at midnight.

---

## Step 12 — Staged scale-up

The 2026-10-06 50% bring-up is not this step's pass. Dispatch was
crash-looping on `dispatch_offers_one_accepted_idx` when the crash
archive was taken (`2026-10-06T09:54:51Z`, 129 restarts). The next
bring-up waits until the three must-fix items under "Fixes before the
next Dionysus run" are in the image. The bootstrap marker note there is
code-only and is not one of the three. The destroy / init / up /
etcd-existing cycle below is unchanged.

**Historical, kept because the reasoning matters: fulfilment was 0.611 at
4,000 drivers with the fleet 96% idle at the same time.** Two readings
agreed on the shape. Over two hours: 1,351 of
5,115 ended trips `no_driver_found`, 26%. Over one hour on the block B
stack: 675 of 3,274, 20.6%. Chain exhaustion cannot explain either - at
0.528 acceptance over five offers, all five refusing happens 2% of the
time, and the measured `offers_per_match` is 1.89. The rest is
`find_candidates` returning nothing at all.

**FOUND, 2026-09-27. It was never a supply problem.** The per-tier GEO
sets on Dionysus held:

```
economy    1871
xl            0
premium       0
```

h-bootstrap seeds the fleet 70/20/10 across the three tiers and writes
exactly one vehicle per driver, so those zeroes are not the data. They are
driver-service reading the roster before the cache was ready.

`main()` waited for the FIRST `driver:*` key to appear, then loaded the
roster once and never again. Vehicle profiles arrive on a different
Debezium topic, so at that moment `vehicle:*` was still empty - and
`load_roster` turned a missing vehicle into an economy car without a word.
One moment decided the tier of all 4,000 drivers for the whole run.

Dispatch matches within the requested tier, so every xl and premium
request found no candidate and ended `no_driver_found`. That is why
raising the fleet from 800 to 4,000 changed nothing: the empty tiers stay
empty at any fleet size, and the economy drivers serving ~70% of demand
sat idle, which is the 0.03-0.04 utilization.

Fixed in `j-service-driver`: the startup gate now waits for BOTH caches to
be full and to stop growing (equal counts is the truth here, not a
threshold, because bootstrap writes one vehicle per driver in the same
loop), and a missing vehicle profile is now fatal and logged rather than
silently defaulted. The service also logs its tier mix at startup, so the
next run says what it loaded instead of leaving it to be inferred from a
GEO set weeks later.

**Re-measured 2026-09-27 on a clean bring-up, and the fix holds.**
driver-service logs `fleet tiers economy 2817, xl 768, premium 415` -
70/20/10, exactly as seeded - and all three GEO sets fill (1379 / 375 /
209). Fulfilment **0.687**, `no_driver_found` **247 of 2,558 ended trips
(9.7%)**, down from 26%. Utilization 0.09, up from 0.03.

So the tier defect is closed. What remains in this step is the supply
ratio below, which is a different thing and genuinely about scale.

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

**Plus two things this step now owns**, both found 2026-10-03:

- **`trips` is unpartitioned and the archiver deletes by row.** Measure
  prune duration per tick and table bloat at both stops. `driver_positions`
  got daily partitions in step 6 for this exact reason and `trips` did not.
  Decide here whether it needs range partitioning on `ended_at` before
  step 13.
- **Surge cannot fire, and scale makes it worse.** See the correction in
  step 11c. Measured now: ~155 drivers per request-per-minute; step 13's
  seed gives ~233. Decide here whether the seed ratio moves or the 0.6
  threshold does, because otherwise "Does surge lift acceptance" is a
  one-band chart at every scale this project will ever run.

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

## Closed by decision — Grafana reads ClickHouse, never Redis

DROPPED 2026-10-02. An earlier plan round called for a second Grafana
datasource reading Redis for the live-ops tier: driver positions on a
Geomap off the `DB_DRIVER` GEO set, free/busy/offline counts, live
passenger requests, and a demand heatmap off `DB_DEMAND`'s `hotspot:*`
scores. It was never built, and the shipped code now argues against it in
two places: `f-infra-grafana/check-dashboards.py:25` rejects any panel
query naming Redis, Kafka or Postgres, and `f-infra-grafana/docker-compose
.yaml` documents the single-datasource rule.

Plan and code had been contradicting each other since, which is the real
reason this is being written down rather than left implied.

Dropped because the warehouse already answers the live questions. The
`nus-live-ops` dashboard runs nine panels - open trips by status, events
and positions per minute, the last two minutes of fleet position and
status - and `make profile` measured `driver_positions` and `trip_events`
at **0 minutes behind** with `hotspot_history` at 1. A Geomap of the last
two minutes of `driver_positions` is the same picture the GEO set holds,
read from the store every other panel already uses.

What the second datasource would have cost: the ClickHouse-only guard
relaxed from a flat ban to an allowlist, a second query language inside
`check-dashboards.py` and `panel-probe.py` (which today proves all 46
panels run by executing their SQL - there is no equivalent for Redis
commands), and the `redis-datasource` plugin pinned and carried in the
image. Three validators lose their single-language assumption to show
something already on screen.

Reversible if a genuinely sub-second panel is ever needed. Nothing has
asked for one: the gap between Redis and the warehouse here is seconds,
and no question on any dashboard turns on it.

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
