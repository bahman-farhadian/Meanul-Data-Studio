# n-service-clickhouse-sink — every event into the analytics database

The last step of the pipeline, and the only writer ClickHouse has while the
stack runs. It reads every topic, adds the context Kafka does not carry, and
inserts in batches.

Built last on purpose: it is the piece that needs data from every other
service before it is worth running at all.

## What it writes where

| Topic | ClickHouse table |
| --- | --- |
| `driver_location` | `nus.driver_positions` |
| `rider_location` | `nus.rider_positions` |
| `trip_lifecycle` | `nus.trip_events` |
| `city_hotspots` | `nus.hotspot_history` |
| `trip_requests` | nothing — see below |

`trip_requests` is consumed but not stored. Every request also appears on
`trip_lifecycle`, either as a match or as `no_driver_found`, so storing both
would count the same trip twice.

## The enrichment

A trip event arrives knowing its own numbers but not the context around it.
The sink adds two things:

- **How busy the pickup zone was**, read from Redis, and whether that makes
  it a hotspot trip.
- **How far the real duration drifted from the predicted one** —
  `duration_delta_s` and `took_longer_than_predicted`. That is the number
  behind the "trips slower than predicted" panel, and it is the honest test
  of whether the traffic-aware routing is any good.

Both come from Redis, never from the OLTP database. That is the rule the
whole stack is built on, and the sink is the busiest reader in it — one
lookup per message against PostgreSQL would be the fastest way to overload
the leader.

Even Redis is not asked per message: demand scores are read in one go and
reused for half a minute, because that is how often they change.

## Batching, and why the timer matters

ClickHouse rewards large inserts and punishes small ones: every insert
creates a part on disk that later has to be merged away. Rows are therefore
collected and sent when the batch is big enough **or** when it has waited
long enough.

The time limit is not a detail. At three in the morning a batch might take
minutes to fill, and a dashboard should not be minutes behind the city.

## Position is saved last

The position in Kafka is committed only after ClickHouse has accepted the
batch. A crash in between repeats a batch, which shows up as a few duplicated
rows; committing first would lose those rows silently, which is far worse in
a table nobody ever re-reads.

If duplicates ever matter for a particular table, the fix belongs in
ClickHouse — a `ReplacingMergeTree` on a key — not in a weaker commit rule
here.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `clickhouse-sink` service. |
| `Dockerfile` | Two stages; runs as a normal user. |
| `clickhouse_sink/batches.py` | The waiting rows, the column orders, and when to send. |
| `clickhouse_sink/__main__.py` | The loop: read, enrich, collect, insert, commit. |
| `.env.example` | Template for the untracked `.env`. |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `SINK_BATCH_ROWS` | `5000` | Rows collected before an insert. |
| `SINK_FLUSH_SECONDS` | `5.0` | How long rows may wait, whatever the count. |
| `SINK_HOTSPOT_REFRESH_SECONDS` | `30.0` | How often demand scores are re-read. |
| `HOTSPOT_SCORE_THRESHOLD` | `0.6` | The score at which a trip counts as a hotspot trip. |
| `CH_*` | `lb-a`, `nus` | ClickHouse through the entry tier; password must match piece e. |
| `KAFKA_*`, `REDIS_*` | see `.env.example` | Connections; passwords must match pieces b and c. |

## Verify

```bash
# rows arriving in the last five minutes
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT 'positions' AS what, count() FROM nus.driver_positions
             WHERE event_time > now() - INTERVAL 5 MINUTE
           UNION ALL
           SELECT 'trip events', count() FROM nus.trip_events
             WHERE event_time > now() - INTERVAL 5 MINUTE"

# the enrichment actually happened
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT status, count(), avg(duration_delta_s), avg(hotspot_score)
             FROM nus.trip_events
            WHERE event_time > now() - INTERVAL 1 HOUR
            GROUP BY status"

# is it keeping up?
docker compose exec kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka-1:9092 --describe --group clickhouse-sink
```

Healthy looks like: row counts rising steadily, `duration_delta_s` and
`hotspot_score` filled in on completed trips, and consumer lag that stays
flat or returns to flat after a burst.

Lag that only grows means one of two things: the batch settings are too
small for the message rate, or ClickHouse is refusing inserts — check its
logs for a memory limit before changing anything here.

## Where the numbers end up

The hourly summary that Grafana and Superset read is filled automatically by
a materialized view inside ClickHouse as these rows land, so nothing here has
to maintain it. That view runs per node, which is why every query against
`nus.trip_stats_hourly` aggregates — see
[`../e-infra-clickhouse/README.md`](../e-infra-clickhouse/README.md#reading-the-hourly-summary-correctly).
