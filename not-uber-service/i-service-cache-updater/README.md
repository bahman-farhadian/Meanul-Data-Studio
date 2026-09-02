# i-service-cache-updater — PostgreSQL changes into Redis

The service that makes the cache trustworthy. Debezium turns every insert,
update and delete in the OLTP database into a message on a `cdc.*` topic
(piece `d`); this service applies those messages to Redis.

Because it exists, every other service can read profiles and reference data
from Redis and never query the PostgreSQL cluster for them — which is the
rule in section 1 of the main README.

## What it writes

| Topic | Redis key | Holds |
| --- | --- | --- |
| `cdc.drivers` | `driver:{id}` | the driver row as it now is |
| `cdc.passengers` | `passenger:{id}` | the passenger row |
| `cdc.trips` | `trip:{id}` | the stored trip row |
| `cdc.city_zones` | `zone:{id}` | the zone |

`trip:{id}` is **not** the same as `trip:{id}:active`. This one is the
database record; the other is the live state `dispatch-service` keeps while
a trip is running. Two different things, two different owners.

Messages about a table that is not in the table above are counted and
skipped, not logged one by one — during Debezium's first pass that would be
thousands of identical lines.

## Three properties, on purpose

**Replaying is harmless.** Each message is written as "the row now looks like
this", never "add one to this". Applying the same message twice gives exactly
the same result, so a restart that repeats a few messages costs nothing. This
is what the main README means by idempotent, last-write-wins upserts.

**It does not wait for bootstrap.** Every other service waits for
`system:bootstrap:done` before doing any work. This one starts consuming
immediately: it is the thing that fills the cache the others read, so waiting
would be a service waiting for itself.

**Position is saved after writing, never before.** Kafka is told how far we
got only once Redis has accepted the batch. A crash in between repeats a few
changes, which is harmless here. Saving first would lose them, which is not.

## How the cache fills itself

Nobody preloads Redis. When the connector is registered after `h-bootstrap`,
Debezium's first pass emits one message per existing row, and this service
turns each of them into a Redis key. The cache is therefore built by the same
code path that keeps it up to date afterwards — one mechanism, not two.

A delete arrives as an ordinary delete message followed by a **tombstone**, a
message with the same key and no value at all. Both lead to the key being
removed.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `cache-updater` service. |
| `Dockerfile` | Two stages; the runtime image has no build tools and runs as a normal user. |
| `pyproject.toml` / `uv.lock` | Dependencies, pinned. |
| `cache_updater/__main__.py` | The whole service: read, apply, save position. |
| `.env.example` | Template for the untracked `.env`. |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP` | the three brokers | Where Kafka is. |
| `SCHEMA_REGISTRY_URL` | `http://schema-registry:8081` | Where the message schemas are explained. |
| `CACHE_UPDATER_GROUP_ID` | `cache-updater` | The consumer group; changing it re-reads from the start. Each service has its own variable so a single master `.env` cannot give two of them the same group. |
| `CDC_TOPIC_PATTERN` | `^cdc\..*` | Which topics to follow. |
| `REDIS_*` | Sentinel set, `nus-cache` | Where the cache is; the password must match `b-infra-redis/.env`. |
| `CACHE_BATCH_SIZE` / `CACHE_FLUSH_SECONDS` | `500` / `2.0` | How much is collected before writing and saving position. |
| `LOG_LEVEL` | `INFO` | `DEBUG` shows every message. |

## Verify

The important question is not "is it running" but "is it keeping up".

```bash
# consumer lag: how many messages are waiting. A number that stays small,
# or returns to small after a burst, is healthy. One that only grows is not.
docker compose exec kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka-1:9092 --describe --group cache-updater

# a change in the database should appear in Redis within a second or two
docker compose exec pg-1 psql -U postgres -c \
  "UPDATE drivers SET rating = 4.9 WHERE driver_id = 'drv-000001';"

docker compose exec redis-1 redis-cli get driver:drv-000001

# how many keys of each kind the cache holds
docker compose exec redis-1 redis-cli --scan --pattern 'driver:*' | wc -l
```

If the value in Redis does not change, check the connector first — this
service can only apply what Debezium sends:

```bash
docker compose exec debezium-connect curl -s http://localhost:8083/connectors/nus-pg/status
```

## Rebuilding the cache from scratch

Because the `cdc.*` topics are compacted, Kafka always holds a current copy
of every row. So the cache can be rebuilt without touching the OLTP
database at all:

```bash
# stop the service, forget its position, start it again
docker compose stop cache-updater
docker compose exec kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka-1:9092 --group cache-updater --reset-offsets \
  --to-earliest --all-topics --execute
docker compose start cache-updater
```

It reads everything again and writes the same keys with the same values.
