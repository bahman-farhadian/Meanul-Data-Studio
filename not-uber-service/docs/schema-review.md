# Schema review — what a real ride-hail platform models, and what we do not

Step 2 of `TODO.md`. Writing only; no server time, no code.

This is not a review of a schema I had already decided was fine. Every
finding below starts from a published description of how a real platform
(or a real reference model, or a real modelling discipline) handles the
same problem, and only then asks what this repo does. Where our answer is
defensible I say so and reject the change — three of the findings below
end in "keep what we have", with the reason.

Read the sources first; the findings are meaningless without them.

---

## 1. What was read

| Source | What it contributed |
|---|---|
| [Uber — H3: Uber's Hexagonal Hierarchical Spatial Index](https://www.uber.com/us/en/blog/h3/) | Why Uber does *not* measure supply/demand in operator-drawn polygons; surge is computed per hexagon; sixteen resolutions, each 1/7 the area of the next coarser |
| [Uber — Upfront Fares: No Math and No Surprises](https://www.uber.com/in/en/blog/upfront-fares-no-math-and-no-surprises-2/) and [Ride Prices and Rates](https://www.uber.com/us/en/ride/how-it-works/upfront-pricing/) | Upfront price includes estimated time and distance, route demand, tolls, taxes, surcharges and fees — but *not* wait-time fees, which settle afterwards |
| [Uber Help — How is the price of a trip determined?](https://help.uber.com/riders/article/how-are-fares-calculated/?nodeId=d2d43bbc-f4bb-4882-b8bb-4bd8acf03a9d) and [RideWise — How Uber & Lyft surge pricing works](https://getridewise.com/blog/uber-lyft-surge-algorithm-explained-2026) | A US fare is base + per-mile x distance + per-minute x duration + booking fee, then a surge multiplier — four named components, not one number |
| [Uber Developers — Dispatch and cancellation](https://developer.uber.com/docs/guest-rides/guest-ride-api-build-guide/dispatch-and-cancellation) | The real state names: Processing, Accepted, **Arriving**, In Progress, Completed; terminal states `no_drivers_available` and `driver_canceled`; a driver who cancels triggers redispatch |
| [Uber Engineering — Schemaless part one](https://eng.uber.com/schemaless-part-one-mysql-datastore/) / [part two](https://eng.uber.com/schemaless-part-two-architecture/) / [part three: datastore triggers](https://www.uber.com/us/en/blog/schemaless-part-three-datastore-triggers/) | Uber moved trips off a single Postgres instance onto an append-only, immutable-cell store; trip processing runs off *triggers on the datastore*, i.e. the change log is the integration point, not the table |
| [drawDB — Uber ride-hailing schema example](https://www.drawdb.app/examples/uber-ride-hailing) | A complete 14-table reference model: `documents`, `driver_sessions`, `surge_zones`, `surge_multipliers`, `ride_requests`, **`dispatch_offers`**, `trips`, `trip_waypoints`, `location_pings`, **`fare_components`**, `ratings` |
| [ShriniwasAhirrao/Uber-Database-Management-System](https://github.com/ShriniwasAhirrao/Uber-Database-Management-System), [srddy/Uber-Database-Design](https://github.com/srddy/Uber-Database-Design) | Independent confirmation of the same entity set — users, vehicles, rides, payments, ride types with their own pricing |
| [ScienceDirect — Ride acceptance behaviour of ride-sourcing drivers](https://www.sciencedirect.com/science/article/pii/S0968090X22002121) ([preprint](https://arxiv.org/pdf/2107.07864)) | Driver response rate is a headline platform KPI; pickup time depresses acceptance; surge and guaranteed tips raise it |
| [ScienceDirect — Order dispatching strategy and pricing with service cancellation](https://www.sciencedirect.com/science/article/abs/pii/S0191261525001158), [Freelance drivers with a decline choice](https://www.sciencedirect.com/science/article/pii/S0191261524002066) | An offer has a deadline (~20s in the studied platforms); on decline the request goes to the next driver — the offer chain is the object, not the assignment |
| [Modern Treasury — Enforcing immutability in your double-entry ledger](https://www.moderntreasury.com/journal/enforcing-immutability-in-your-double-entry-ledger), [Dodo Payments — Payment ledger design](https://dodopayments.com/blogs/payment-ledger-design), [Fynex — Marketplace payout ledger](https://fynex.ai/blog/marketplace-payout-ledger/) | Money is an append-only journal; balances are derived by replay; corrections are reversing entries, never updates; commission, refunds and tax each arrive on their own timeline |
| [Medium — Envelope patterns for Kafka event schemas](https://medium.com/@zdb.dashti/1-designing-event-schemas-for-long-lived-contracts-48a5cc8bb2c7), [AlgoMaster — Designing good Kafka events](https://algomaster.io/learn/kafka/designing-good-kafka-events), [Conduktor — Schema evolution best practices](https://www.conduktor.io/glossary/schema-evolution-best-practices) | An event carries an envelope: `event_id`, `occurred_at`, type, version, correlation/trace id — the `event_id` is what a consumer inbox dedupes on |
| [Altinity — Decimals vs Floats in ClickHouse](https://altinity.com/blog/decimals-vs-floats-in-clickhouse) and the [Altinity KB entry](https://kb.altinity.com/altinity-kb-schema-design/floats-vs-decimals/) | `Float64` summation is order-dependent and non-exact (`499693.60500000004` vs `499693.605`); money belongs in `Decimal64(2)` |
| [Kimball Group — Dimensional modeling techniques (PDF)](https://www.kimballgroup.com/wp-content/uploads/2013/08/2013.09-Kimball-Dimensional-Modeling-Techniques11.pdf), [Type 2: add new row](https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/type-2/) | The three fact grains; a trip moving through milestones is the textbook **accumulating snapshot**, not a transaction-grain event log |

Two of these are worth calling out as *not* sources of design authority:
the GitHub "uber clone" app repos are student/tutorial projects and are
used here only as corroboration that a given entity keeps appearing, never
as the reason to build something. The drawDB example is a reference model,
not a production schema — the same caveat applies, and I say where I
follow it and where I do not.

---

## 2. What we actually have

12 Postgres migrations, 6 Avro records, 6 Kafka topics, 9 ClickHouse DDL
files. Summarised so the findings can point at it:

- `drivers`, `passengers`, `vehicles`, `trips`, `trip_ratings`,
  `driver_sessions`, `city_zones`, `segment_traffic`,
  `zone_demand_calibration`, `od_pair_calibration`.
- `trips` carries the whole lifecycle in one row: `status` (9 symbols),
  six timestamps, `route`/`route_km`, predicted vs actual duration,
  `surge_multiplier`, `fare_estimate`, `fare_final`, `driver_payout`,
  `payment_method`, `cancellation_reason`, `requested_vehicle_type`.
- Kafka: `driver_location`, `rider_location`, `trip_requests`,
  `trip_lifecycle`, `city_hotspots`, `segment_traffic_updates`.
- ClickHouse: `driver_positions`, `rider_positions`, `trip_events`,
  `hotspot_history`, `segment_traffic_history`, plus five rollups
  (`trip_stats_hourly`, `trip_stats_daily`, `od_matrix_daily`,
  `trip_duration_percentiles_hourly`, `active_entities_hourly`,
  `driver_utilization_hourly`).

The schema is genuinely good in places, and those places should not be
disturbed: the Enum8/CHECK/Avro three-copy rule, `FixedString` for ids we
mint ourselves vs `LowCardinality(String)` for TLC's, the honest
SummingMergeTree-vs-AggregatingMergeTree split in `005`/`006`, and the
per-table TTL reasoning in `002` are all better than what the reference
models show. The findings below are about what is *missing*, not about
undoing that.

---

## 3. Findings

Ordered by how much each one changes what the platform can answer.

### F1 — Four columns Postgres knows never reach Kafka or the warehouse

**What we have.** `driver_payout`, `payment_method`, `cancellation_reason`
and `requested_vehicle_type` exist in `trips` (migrations `006`, `009`,
`011`) and are written by dispatch-service. Verified by grep: none of the
four appears in any `.avsc`, and none appears in any ClickHouse DDL file.
`trip_lifecycle.avsc` carries 13 fields; those four are not among them.

**Consequence.** The warehouse — which is the *only* thing Superset is
allowed to chart — cannot answer:

- take rate (`fare_final` vs `driver_payout`), the single most important
  marketplace economics number;
- payment mix (card / wallet / cash);
- *why* trips cancel, even though we deliberately built a six-value
  reason vocabulary in `009`;
- anything per vehicle tier, even though `vehicles.vehicle_type` and
  `trips.requested_vehicle_type` both exist and dispatch matches on it.

This is not a design disagreement; it is drift between three copies that
the repo's own three-copy rule exists to prevent, and the existing
`test_assessment_standard.py` guard did not catch it because it checks
*symbol sets that exist in all three*, not *columns that exist in one*.

**Proposal — ACCEPT, highest priority.**
Add to `trip_lifecycle.avsc` (all optional with defaults, so the Schema
Registry accepts it as a backward-compatible evolution): `driver_payout`,
`payment_method` (enum `card|wallet|cash`), `cancellation_reason` (enum,
nine symbols matching the CHECK), `requested_vehicle_type` (enum
`economy|xl|premium`). Mirror into `trip_events_local` as
`Nullable(Decimal64(2))` and three `Enum8`s (`Nullable` on the two that
are genuinely optional). Extend the drift test to compare *column names*
across the three stores for the trip entity, not just enum symbols.

**Cost.** One Avro revision, one `ALTER TABLE ... ON CLUSTER`, a sink
mapping, one test. No new table, no new topic, no generator work —
dispatch already computes all four.

---

### F2 — There is no `arrived` state, so wait-time economics do not exist

**What real platforms do.** Uber's own dispatch documentation names
**Arriving** as a first-class state — "the driver has arrived or is within
0.2 miles of the pickup point" — and Uber's upfront-pricing page is
explicit that wait-time fees are the one component *excluded* from the
upfront price, i.e. they are settled from observed arrival-to-start time.
The drawDB reference model carries `trips.arrived_at` alongside
`accepted_at` / `started_at`.

**What we have.** Nine statuses: `requested`, `matched`, `accepted`,
`en_route_pickup`, `in_progress`, `completed`, and three terminal
cancel/no-driver symbols. `en_route_pickup` ends and `in_progress` begins
in one transition (`l-service-dispatch/dispatch_service/trips.py`), so the
moment the car reaches the kerb is never recorded.

**Consequence.** Rider wait time at pickup is underivable. So is the
distinction between "cancelled while the driver was still driving over"
and "cancelled after the driver was waiting outside" — which is precisely
the line a real platform draws to charge a cancellation fee. We already
ship `rider_no_show` and `wait_too_long` as cancellation reasons; today
those are *asserted* by the generator and can never be *checked* against
the data, which makes them decoration rather than signal.

**Proposal — ACCEPT.**
Add `arrived` to the status set in all three copies (Postgres CHECK, Avro
`TripStatus`, ClickHouse `Enum8` — note this means renumbering or
appending; append as `= 10` so existing Enum8 data stays valid), add
`trips.arrived_at timestamptz`, and emit the transition from dispatch's
state machine with a realistic dwell before `in_progress`. Derived
measures: `wait_s = started_at - arrived_at`, and a cancellation is
"post-arrival" iff `arrived_at IS NOT NULL`.

**Cost.** Small, but it touches the state machine, which is the one place
a mistake shows up in every downstream count. Do it in the same commit as
the rollup that consumes it, not before.

---

### F3 — Money is one scalar, stored as `Float64` in the warehouse

**What real platforms do.** A US Uber fare is four named components — base
fare, per-mile x distance, per-minute x duration, booking fee — multiplied
by surge, *plus* tolls, taxes and surcharges. The drawDB reference models
this as a `fare_components` child table: `(trip_id, kind, amount_minor,
currency_code, quantity, unit_rate_minor, taxable)`. One row per component,
so a receipt is a `SELECT`, not an inference.

**What we have.** `l-service-dispatch/dispatch_service/pricing.py`
computes `(base + per_km*km + per_minute*min) * surge` and returns a single
rounded float. `trips` stores `fare_estimate`, `fare_final`,
`surge_multiplier`, `driver_payout` — four `numeric(10,2)` columns. There
is no booking fee, no toll, no tax, no tip, no promo, no cancellation fee,
and no way to add one without changing the meaning of `fare_final`.

**What is actually wrong, beyond missing components.** In ClickHouse the
same money is `Nullable(Float64)`, and `revenue Float64` in three
`SummingMergeTree` rollups. Altinity's measurement of exactly this case:
summing `Float64` yields `499693.60500000004` where `Decimal64(3)` yields
`499693.605`. SummingMergeTree performs that sum during background merges,
in an order nobody controls, so **our revenue figure is not deterministic
at the cent level and two replicas can legitimately disagree**. This is
the clearest outright defect in the schema.

**Proposal — ACCEPT, in two independent pieces.**

*3a (do first, cheap, no new tables):* every money column in ClickHouse
becomes `Decimal64(2)` — `fare_estimate`, `fare_final`, `driver_payout`
(after F1), `revenue` in `trip_stats_hourly_local`,
`trip_stats_daily_local`, `od_matrix_daily_local`. This also restores the
three-copy rule: Postgres is already `numeric(10,2)`; `Float64` was never
its match. Keep `surge_multiplier` as `Float64` — it is a ratio, not money.

**The wire is the third copy, and it is a `double` today.**
`trip_lifecycle.avsc` declares `fare_estimate` and `fare_final` as Avro
`double`. A *single* two-decimal value does survive a double round-trip
intact at our magnitudes, so this is not a live defect the way the
ClickHouse sums are — but "two decimals everywhere" is not true of a
schema that says `double`, and a contract that has to be explained is
not a contract. The money fields become Avro `decimal`:
`{"type": "bytes", "logicalType": "decimal", "precision": 10, "scale": 2}`,
matching Postgres's `numeric(10,2)` exactly.

*Needs one local check before it is planned, not assumed:* this stack
serialises through `confluent-kafka[schema-registry]` over `fastavro`
(`z-lib/nus-common/pyproject.toml`), and fastavro does implement the
decimal logical type — but round-tripping a `Decimal("12.22")` through
`AvroSerializer`/`AvroDeserializer` against the real Registry is a
five-minute local test and should be run before any `.avsc` is changed.
Debezium's own Avro converter already emits Postgres `numeric` as a
scaled-bytes decimal on the `cdc.*` topics, so the encoding is believed
to be in use in this stack already — believed, not verified.

*3b:* a `fare_components` table in Postgres, `(trip_id, kind, amount,
quantity, unit_rate)` with `kind` a CHECK-constrained closed set:
`base`, `distance`, `time`, `booking_fee`, `surge_premium`, `toll`,
`tax`, `tip`, `promo`, `wait_time`, `cancellation_fee`. `fare_final`
stays on `trips` as the denormalised total, with an invariant worth
asserting in `make verify-data`: the components sum to it. Only `base`,
`distance`, `time`, `surge_premium` and `booking_fee` need to be generated
on day one; the rest become the natural home for F2's wait fee and F4's
refunds without another migration.

**Deliberately not adopted from the reference:** integer minor units
(`amount_minor INTEGER`). Postgres `numeric(10,2)` is exact already, and
switching representations would break every existing query and every
generator for no accuracy gain. `Decimal64(2)` on the ClickHouse side is
the same decision expressed in ClickHouse's own type system. Currency:
add `currency_code CHAR(3) NOT NULL DEFAULT 'USD'` — this is a NYC-only
simulation, but the column is nearly free and its absence is the thing
that makes multi-city impossible later.

---

### F4 — Payment is two columns, not an append-only ledger

**What real platforms do.** Every marketplace-ledger source read for this
says the same thing in different words: the journal is append-only,
balances are *derived by replay*, and a correction is a reversing entry —
you never update a row that has already been written. Commission tiers,
refunds, partial refunds and tax withholding each arrive on their own
timeline, days apart from the trip.

**What we have.** `payment_method text` and `driver_payout numeric(10,2)`,
added together in migration `011`, mutable, one value each, forever.

**Consequence.** A failed card, a retry, a refund, a tip added after the
ride, a promo credit, an adjustment, or a driver incentive cannot be
represented. More importantly, because they are mutable columns, the *only*
way to represent any of those later is to overwrite the number — which
destroys the history the whole warehouse exists to keep.

**Proposal — ACCEPT the minimum, REJECT the full ledger for v1.**

A full double-entry ledger belongs in v2 (already parked in `TODO.md` as
"a high data intensive financial service with fraud detection") and
building half of it now would mean building it twice. What v1 needs is the
*shape* that makes v2 additive rather than a rewrite:

- `payments` — one row per charge **attempt**: `(payment_id, trip_id,
  method, amount, status, provider_ref, created_at)` with `status` in
  `authorized|captured|failed|refunded`. Append-only: a refund is a second
  row referencing the first, never an update of it.
- `driver_earnings` — one row per payout-affecting event:
  `(earning_id, trip_id, driver_id, kind, amount, created_at)` with `kind`
  in `trip_fare|tip|incentive|adjustment|cancellation_fee`. `driver_payout`
  on `trips` stays as the denormalised trip-fare total.

Both are append-only by convention and by the absence of any `updated_at`;
worth enforcing with a `BEFORE UPDATE` trigger that raises, so the
invariant is the database's and not the service's.

**Explicitly rejected for v1:** double-entry with balanced debits and
credits, per-account balances, reversing-entry machinery. Real, correct,
and v2's job.

---

### F5 — There is no matching funnel; a driver can never decline

**What real platforms do.** The reference model's `dispatch_offers` is a
full table: `(request_id, driver_id, session_id, sequence, offered_at,
expires_at, responded_at, status, eta_seconds, distance_to_pickup)` —
note `sequence`, because an offer chain is the normal case. The dispatch
literature describes the same mechanism: a driver has on the order of 20
seconds to accept, and on decline the request moves to the next candidate.
Driver response rate is named as a headline KPI, and pickup time is found
to depress acceptance.

**What we have.** `dispatch_service/trips.py` — "Dispatch owns the status
machine" — assigns a driver and goes straight `matched -> accepted`. There
is no offer, no deadline, no decline. `no_driver_found` is therefore a
decision the generator makes, not the observable end of a search.

**Consequence.** Every question in the matching funnel is unanswerable:
acceptance rate, offers per match, time-to-match, how ETA-at-offer relates
to acceptance, and how deep dispatch had to search in an undersupplied
zone. These are the metrics a real marketplace team looks at first, and
"not uber service" currently has none of them.

**Proposal — ACCEPT, and it is the largest item here.**

- Postgres `dispatch_offers` following the reference columns, with
  `status` in `offered|accepted|declined|expired|cancelled` and a
  `(trip_id, sequence)` unique constraint.
- A new Kafka topic `dispatch_offers`, keyed by `trip_id` so the whole
  offer chain for a request stays ordered on one partition — the same rule
  `trip_lifecycle` already follows.
- ClickHouse `dispatch_offers_local` + a `dispatch_funnel_hourly`
  SummingMergeTree rollup: offers made, accepted, declined, expired, and
  `eta_seconds` sum per pickup zone per hour.
- dispatch-service gains a decline probability that is a real function of
  `eta_seconds` and surge, which is what the acceptance literature
  describes and what makes the resulting data worth charting.

**Cost, stated honestly.** This is the one finding that changes a service's
core loop rather than adding columns. It also multiplies message volume on
a new topic by roughly (offers per match), which at full scale is not
nothing — budget it in step 6's capacity arithmetic, not after.

---

### F6 — Every event is a bare payload; no `event_id`, no correlation id

**What real platforms do.** The envelope pattern: `event_id`, `occurred_at`,
event type, schema version, and a correlation/trace id, with the domain
payload inside. `event_id` is specifically what a consumer's inbox dedupes
on — a stable id does not itself make a consumer idempotent, but nothing
else can.

**What we have.** All six `.avsc` records are flat domain payloads. No
`event_id` anywhere. No correlation id anywhere. No schema version in the
message (the Registry knows it; a consumer reading a ClickHouse row does
not).

**Consequence.** `clickhouse-sink` commits offsets after writing; a crash
between write and commit replays the batch, and a replayed `trip_lifecycle`
message is *indistinguishable* from a genuine repeated status transition —
`trip_events_local` is a plain `ReplicatedMergeTree`, which does not dedupe
across different insert batches. Trip counts can silently overstate.
Separately, nothing links a `trip_requests` message to the
`trip_lifecycle` messages it caused, or either to the `driver_location`
rows produced during that trip, so a single trip cannot be traced across
the pipeline.

This also fails a rule we set ourselves: the global engineering standard in
`CLAUDE.md` §3.3 requires "correlation IDs on anything that crosses a
process boundary".

**Proposal — ACCEPT. Best value-per-line in this document.**

Add four optional-with-default fields to all six records — `event_id`
(string, uuid), `event_version` (int, default 1), `producer` (string,
service name), `correlation_id` (string, = `trip_id` for trip-scoped
streams, the tick id otherwise). Optional-with-default means every existing
consumer keeps working and the Registry accepts it as backward compatible.
Carry `event_id` into every ClickHouse table and add it to the sink's
batch as the dedupe key.

**Cost.** ~16 bytes/message of UUID plus a short string. On
`driver_location` at full scale (~20,000 msg/s per `topics.tsv`'s own
arithmetic) that is real and must be measured, not assumed — if it is too
expensive there, apply the envelope to the four business-event topics and
leave the two high-volume telemetry streams bare, with the reason written
down. Decide it with a measurement in step 6.

---

### F7 — The warehouse has no trip-grain fact, so funnels need self-joins

**What the discipline says.** Kimball names three fact grains —
transaction, periodic snapshot, accumulating snapshot — and defines the
accumulating snapshot as "one updated row per item moving through pipeline
milestones". A trip with requested/matched/accepted/arrived/started/ended
milestones is the canonical example.

**What we have.** `trip_events_local` is transaction grain: one row per
status change, 6+ rows per trip, kept 365 days. Every rollup we built
(`trip_stats_hourly`, `trip_stats_daily`, `od_matrix_daily`,
`trip_duration_percentiles_hourly`) filters `WHERE status = 'completed'`.

**Consequence, and this one is easy to miss.** Because every rollup looks
only at completed trips, **cancellations and unmatched requests are
invisible in every aggregate the warehouse produces**. The fulfilment rate
— completed / requested — is the most basic health metric a ride-hail
platform has, and today it requires scanning raw `trip_events` and
grouping by `trip_id`. Median time-to-match needs a self-join of the same
table against itself, per trip, over a year of data.

**Proposal — ACCEPT.**
`trip_facts_local`, one row per trip, `ReplacingMergeTree(version)` ordered
by `trip_id`: milestone timestamps (`requested_at` … `ended_at`), the
derived lags (`match_s`, `accept_s`, `arrive_s`, `wait_s`, `ride_s`), final
outcome, both zones, both money columns, tier, cancellation reason, and
offer count once F5 lands. Fed either by the sink keeping a small
in-flight map keyed by `trip_id` and writing the row on terminal status,
or by a ClickHouse materialized view over `trip_events_local` using
`argMax` per trip — decide with a measurement, not a preference.

Then `fulfilment_hourly`: requested, matched, completed, cancelled,
no-driver, per zone per hour. One table that makes the funnel a `SELECT`.

---

### F8 — The warehouse has no dimensions at all

**What we have.** Zero dimension tables in ClickHouse. `driver_id` is a
`FixedString(11)` and nothing else. A chart that wants driver tier, home
borough, vehicle make, or zone name has to join back to Postgres — which
the Superset rule in the plan explicitly forbids for dashboards (the
Postgres connection is SQL-Lab-only).

Debezium already publishes `cdc.*` topics for every Postgres table; today
they feed only Redis via cache-updater, never the warehouse.

**Proposal — ACCEPT Type 1 only.**
`dim_driver`, `dim_vehicle`, `dim_zone` as `ReplacingMergeTree(updated_at)`
fed from the existing CDC topics — current state, last write wins. That is
enough to label a chart and to group by borough or tier.

**Explicitly rejected for v1: Type 2 history.** Kimball Type 2 (new row,
new surrogate key, existing facts keep pointing at the old key) is correct
and is what a real analytics platform does for a driver whose tier changed
mid-quarter. It is also a surrogate-key regime across every fact table,
and this schema's ids are natural keys end to end. Not worth the rewrite
for a simulation whose drivers do not change tier. Recorded here so the
omission is a decision, not an oversight.

---

### F9 — Zones are operator-drawn polygons, which is the thing Uber built H3 to stop using

**What Uber says.** The H3 post argues directly against polygonal zones —
"unusual shapes and sizes which are not helpful for analysis", "subject to
change for reasons entirely unrelated" to analysis — and states that Uber
calculates surge "by measuring supply and demand in hexagons in each city
that we serve". Hexagons have exactly one centre-to-neighbour distance,
which is what makes gradients and smoothing well-defined.

**What we have.** `city_zones` is TLC's 263 official Taxi Zones — exactly
the operator-drawn polygons that argument is about. Midtown zones are a few
blocks; some Queens and Staten Island zones are many square kilometres.
`demand_score` is computed per zone and compared across zones as if they
were comparable, and surge from one hot corner is smeared across an entire
large zone.

**Proposal — ACCEPT, as an additive second key. Do not remove zones.**

TLC zones stay and stay primary: they are what `zone_demand_calibration`
and `od_pair_calibration` are keyed on, what TLC's own published data uses,
and what a human reading a dashboard recognises. What is missing is a
fixed-area key *alongside* them.

Add `h3_r8` (`UInt64`) to `driver_positions`, `rider_positions` and
`trip_events`, and an `h3_demand_5min` rollup keyed on it. Resolution 8 is
roughly a city-block neighbourhood and sits inside Uber's stated
operational band of 7–9; confirm the actual average edge length against the
H3 documentation before committing to 8 rather than 9, rather than
trusting this sentence.

**Must be verified before this is planned, not assumed:** ClickHouse ships
H3 functions (`geoToH3` and friends) but they depend on a build option, so
confirm they exist in the exact image this stack runs — `SELECT
geoToH3(-73.98, 40.75, 8)` against the live cluster — before any DDL is
written. If they are absent, the cell id is computed producer-side in
Python instead, which is a different (and larger) change.

---

### F10 — Driver eligibility is not modelled

**What real platforms do.** The reference model has a `documents` table —
`(driver_id, vehicle_id, kind, document_ref, issued_on, expires_on, status,
reviewed_at, rejection_reason)` — and carries `licence_number`,
`licence_country_code`, `background_check_at` and `payout_account_ref` on
the driver. Document expiry is one of the real reasons supply drops.

**What we have.** A driver is a name, a phone, a rating, a status and a
home zone. Nothing can make a driver ineligible. `vehicles` has no
`is_active`.

**Proposal — ACCEPT, but below the line.**
`driver_documents` with `expires_on`, plus `vehicles.is_active boolean`.
Cheap in schema terms; the real cost is that h-bootstrap must generate
plausible documents and driver-service must honour expiry, for analytical
value that is genuinely second-order next to F1–F7. Build it only if steps
3–5 come in under budget.

---

### F11 — `passenger_count` is produced and thrown away

**What we have.** `trip_requests.avsc` carries `passenger_count` (default
1); passenger-service produces it. There is no `passenger_count` column in
`trips`, none in `trip_events`, and the sink consumes `trip_requests`
without storing it. Meanwhile `vehicles.seats` exists and
`requested_vehicle_type` exists, and nothing anywhere checks that a party
of five is not matched to a four-seat economy car.

**Proposal — ACCEPT, trivially.**
`trips.passenger_count smallint NOT NULL DEFAULT 1`, the same field on
`trip_lifecycle.avsc` and `trip_events`, and a dispatch-side check that
`passenger_count <= vehicles.seats` for the matched vehicle. A one-line
data-quality bar in step 7: zero rows where it does not hold.

---

### F12 — Calibration has no provenance, and surge has no validity window

**What we have.** `zone_demand_calibration` and `od_pair_calibration`
record weights with no indication of which TLC month produced them. Two
bootstraps calibrated from different months are indistinguishable in the
data. `segment_traffic` is overwritten in place (ClickHouse's
`segment_traffic_history` partly compensates, but Postgres keeps nothing).
The reference model's `surge_multipliers` carries `effective_from` /
`effective_to`; ours exists only as a number copied onto each trip and a
Redis key with a six-hour TTL.

**Proposal — ACCEPT the cheap half, REJECT the rest.**

*Accept:* `source_month date` and `calibrated_at timestamptz` on both
calibration tables. Two columns, real provenance, no runtime cost.

*Reject:* a Postgres `surge_multipliers` table with validity windows.
`hotspot_history` in ClickHouse already records surge per zone per period
with `computed_at`, at 365-day retention — the history exists, it is simply
in the warehouse rather than the OLTP store, which is where this
architecture wants it. Adding a second copy in Postgres would be a third
copy of a number that already has two.

---

### F13 — `trips` is both the request and the trip — and that is fine

The reference model splits `ride_requests` from `trips`: a request that
never matched is its own row with its own status, and a `trip` row only
exists once a driver accepted. Ours merges them, with `no_driver_found`
as a `trips` status and `driver_id` nullable forever.

**Verdict — REJECT the split. Keep the merged table.**

The split's real benefit is that unmatched demand is a first-class object
you can count without a filter. F5 (`dispatch_offers`) and F7
(`trip_facts` + `fulfilment_hourly`) deliver exactly that benefit without
rewriting the table every service and every generator already writes to.
The split's cost is a migration that renumbers the primary business entity
of the whole system, plus double-writing across two tables in dispatch.
Wrong trade.

The consequences of keeping it should be written down rather than
forgotten: `trips.driver_id` is nullable by design, `trip_id` is minted
before a trip exists, and `count(*) FROM trips` is meaningless without a
status filter. That belongs in the schema documentation in step 11.

---

### F14 — `trip_ratings` never reaches the warehouse

**What we have.** Migration `008` builds a proper bidirectional
`trip_ratings` table, and dispatch-service writes it
(`dispatch_service/ratings.py`). Verified by grep: no Avro record, no
Kafka topic, no ClickHouse table. Superset cannot chart rating
distribution, rating by zone, or rating by tier.

Related and worth naming: `drivers.rating` and `passengers.rating` are
denormalised aggregates of `trip_ratings`, recomputed in bulk, with no
column recording when they were last recomputed.

**Proposal — ACCEPT the cheap path.**
Ratings are low-volume and arrive after the trip ends. Rather than a new
topic and a new producer, fold them into F8's CDC path: `trip_ratings`
already has a `cdc.*` topic from Debezium, so a `trip_ratings_local`
ReplacingMergeTree fed the same way as the dimensions costs one DDL file
and one consumer branch. Add `rating_updated_at` to `drivers` and
`passengers` so the denormalisation is dated rather than mysterious.

---

## 4. Priority

**Tier 1 — build in steps 3–5. Without these the platform cannot answer
questions a ride-hail platform is expected to answer.**

1. F1 — the four orphaned columns reach Kafka and ClickHouse
2. F3a — money is `Decimal64(2)` in ClickHouse and `decimal(10,2)` on the
   wire, matching Postgres's `numeric(10,2)`
3. F6 — event envelope on **all six** topics (decided, see §6)
4. F7 — `trip_facts` + `fulfilment_hourly`
5. F2 — the `arrived` state and `arrived_at`
6. F5 — `dispatch_offers`, with real decline and expiry (decided, see §6;
   promoted out of tier 2)
7. F11 — `passenger_count` survives to storage

**Tier 2 — build if steps 3–5 come in under budget.**

8. F3b — `fare_components`
9. F4 — `payments` + `driver_earnings`, append-only
10. F8 — Type 1 dimensions from CDC
11. F14 — `trip_ratings` into the warehouse

**Tier 3 — record, do not build for v1.**

12. F9 — H3 second key (blocked on verifying ClickHouse H3 support)
13. F12 — calibration provenance (trivial; fold into any Tier-1 migration)
14. F10 — driver documents

**Rejected, with reasons above:** the `ride_requests`/`trips` split (F13),
full double-entry ledger (F4), Kimball Type 2 dimensions (F8), a Postgres
`surge_multipliers` table (F12), integer minor units for money (F3).

---

## 5. What this changes in the plan

Steps 3–5 of `TODO.md` were written against a six-item list that predated
this research. They should be rewritten against Tier 1 and Tier 2 above.
Two items on that old list survive unchanged (`arrived_at` is F2,
`payments` is F4); one is absorbed (item 6 on denormalised ratings becomes
part of F14); one — partitioning `trips` by month on `requested_at` — is
not a schema *design* question at all and belongs in step 6's capacity
work, where the archiver already lives.

## 6. Decisions taken — 2026-09-25

Three questions were left open when this review was written. All three are
now settled, and the tiers in §4 already reflect them.

**F5 — drivers can refuse. Build the full mechanism, not a log-only
version.** An offer is made to one driver with a deadline; the driver may
accept, decline, or let it expire, and the request then moves to the next
candidate. This is promoted out of tier 2 into tier 1 — it is no longer
"if budget allows". The consequence to plan for is volume: the
`dispatch_offers` topic carries several messages per completed match, not
one, so it must be sized in step 6 alongside the position streams rather
than treated as a low-volume business topic.

**F6 — the envelope goes on all six topics, including the two
high-volume position streams.** No measurement gate, no fallback to a
four-topic subset. The reasoning is worth writing down because it is a
sharper argument than the one this review originally made for it:
ClickHouse does not guarantee deduplication. `ReplacingMergeTree` dedupes
only eventually, only within a partition, and only on merge; the
`insert_deduplication_token` path only covers an identical retried block.
None of that is a foundation for correct counts. So uniqueness has to be
guaranteed *before* the warehouse — a unique `event_id` on every message
and a sink that rejects what it has already seen — and only then is
aggregating inside ClickHouse trustworthy. That argument applies to
`driver_positions` exactly as much as to `trip_events`, which is why the
subset option is dropped. The byte cost is now a capacity input to step 6,
not a decision gate.

**Money is two decimals, everywhere.** `numeric(10,2)` in Postgres,
Avro `decimal` with precision 10 and scale 2 on the wire, `Decimal64(2)`
in ClickHouse. One representation, no `double` hop in the middle. See
F3a, which now covers the wire as well as the warehouse.

Nothing else in this document is waiting on an answer.
