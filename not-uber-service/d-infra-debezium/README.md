# d-infra-debezium — change data capture from PostgreSQL

Kafka Connect running the **Debezium PostgreSQL connector**. It turns every
insert, update and delete in the OLTP database into a Kafka message, so the
rest of the stack can react to database changes without anyone writing
extra code into the services.

## How it works, in short

PostgreSQL writes every change into a journal before applying it — the
**write-ahead log**, or WAL. Debezium asks PostgreSQL for a copy of that
journal as it is written, using the same mechanism a replica uses. So:

- nothing polls the database with `SELECT` loops;
- the services do not have to publish "I changed a row" messages;
- the order of changes is exactly the order the database applied them.

The first time it connects, Debezium takes a **snapshot**: it reads the
tables as they are and emits one message per existing row. After that it
only streams changes. That snapshot is what fills Redis after
`h-bootstrap` seeds the database — nobody has to preload the cache.

Each change becomes a message on `cdc.<table>`, keyed by the row's primary
key, and `i-service-cache-updater` applies it to Redis.

## Message shape

The messages keep Debezium's full envelope: `before`, `after`, `op`
(`c` create, `u` update, `d` delete, `r` read-during-snapshot) and `source`
metadata. The envelope is not flattened on purpose — a consumer that can see
both the old and the new row, and the operation itself, can handle a delete
correctly instead of guessing from an empty field.

A delete is followed by a **tombstone**: a message with the same key and no
value. That is the marker `cache-updater` turns into a Redis `DEL`, and it
is also what lets Kafka forget the key when it compacts the topic.

## Why the cdc.* topics are compacted

Ordinary topics keep 48 hours of messages and then drop the oldest. The
`cdc.*` topics are **compacted** instead: Kafka keeps the newest message per
key for as long as the key exists. The result is that the topic always holds
a complete, current copy of the table, so the cache can be rebuilt from
Kafka alone at any time, without touching the OLTP database.

## Order of operations

The connector names the tables it follows, so those tables must exist before
it is registered.

1. piece `d`: bring `debezium-connect` up (this file);
2. piece `h`: let `h-bootstrap` create and seed the tables;
3. then run `docker compose run --rm connector-register`.

Running the one-shot too early fails with a clear error from Connect, which
is better than a connector that silently follows nothing.

## The replication slot — the thing to watch

To keep a copy of the journal for Debezium, PostgreSQL creates a
**replication slot** (`nus_debezium`). While that slot exists, PostgreSQL
**refuses to delete WAL that the slot has not read yet**. That is exactly
what makes CDC reliable, and also the one way this component can hurt the
database: if Connect is stopped for a long time and the slot is left behind,
WAL piles up until the disk fills.

So:

- while the stack runs, check the slot's lag now and then (see
  [Verify](#verify));
- if this component is removed for good, **drop the slot**, do not just stop
  the container (see [Teardown](#teardown));
- `heartbeat.interval.ms` is set so an idle stack still moves the slot
  forward instead of looking stuck.

## Connector settings that matter

| Setting | Value | Why |
| --- | --- | --- |
| `plugin.name` | `pgoutput` | PostgreSQL's built-in logical decoding output. Nothing extra to install in the database image. |
| `slot.name` / `publication.name` | `nus_debezium` / `nus_pub` | Named on purpose, so an operator can find and drop them by hand. |
| `publication.autocreate.mode` | `filtered` | Debezium publishes only the listed tables, not everything. This keeps the huge OSM routing tables out of Kafka. |
| `table.include.list` | drivers, passengers, trips, city_zones | The tables the cache actually needs. |
| `snapshot.mode` | `initial` | Read what is already there once, then stream. This is what fills the cache after seeding. |
| `tombstones.on.delete` | `true` | A delete leaves a null-valued message, which becomes a Redis `DEL` and lets Kafka compact the key away. |
| `decimal.handling.mode` | `double` | Fares arrive as plain numbers instead of encoded decimals, so consumers need no special decoding. |
| `time.precision.mode` | `connect` | Timestamps arrive as ordinary millisecond values. |
| `transforms.route` | `nus.public.X` -> `cdc.X` | Debezium's default topic name carries the server and schema. The rename gives the short names used everywhere else in the stack. |
| `topic.creation.*` | 3 partitions, 3 copies, compacted | Broker-side auto-creation is off, so Connect must create its own topics with the right settings. |

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `debezium-connect` plus the `connector-register` one-shot behind the `init` profile. |
| `Dockerfile` | Debezium's Connect image with Confluent's Avro converter added, so the `cdc.*` topics use the same encoding as every other topic. |
| `connectors/nus-pg.json` | The connector definition. No password: it is injected at registration. |
| `connectors/register.py` | Sends the definition to Connect (PUT, so re-running updates it) and prints the resulting status. |
| `.env.example` | Template for the untracked `.env` (image pins, database connection, password). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone — the whole stack runs UTC. |
| `CONNECT_IMAGE` | `quay.io/debezium/connect:3.1` | Base image for the build. |
| `AVRO_CONVERTER_VERSION` | `8.0.0` | Confluent Avro converter version; keep it in step with the Schema Registry. |
| `PYTHON_IMAGE` | `python:3.13-slim` | Image used by the registration one-shot. |
| `CDC_PG_HOST` / `CDC_PG_PORT` | `lb-a` / `5432` | Where to read from — the write port, which always points at the current leader. |
| `CDC_PG_DATABASE` / `CDC_PG_USER` | `postgres` / `postgres` | Database and login. The user must be allowed to read the WAL. |
| `CDC_PG_PASSWORD` | — (required) | Must match `PG_SUPERUSER_PASSWORD` in `a-infra-postgres/.env`. |

## Known limitation: one hostname

Every other client in the stack lists both proxies (`lb-a,lb-b`) and fails
over on its own. Debezium accepts a single `database.hostname`, so it
cannot. If `lb-a` is lost, change `CDC_PG_HOST` to `lb-b` in `.env` and run
`connector-register` again; the connector picks up where it stopped, because
its read position lives in Kafka, not in the container.

## Verify

```bash
# the connector exists and is RUNNING, and so is its task
docker compose exec debezium-connect curl -s http://localhost:8083/connectors/nus-pg/status

# the cdc.* topics Debezium created
docker compose exec kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka-1:9092 --list | grep '^cdc\.'

# watch a change arrive: update a row, then read the topic
docker compose exec pg-1 psql -U postgres -c \
  "update drivers set status = 'idle' where driver_id = (select driver_id from drivers limit 1);"

docker compose exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --property schema.registry.url=http://schema-registry:8081 \
  --topic cdc.drivers --max-messages 1
```

Slot health, from the database side:

```bash
# active should be true, and the lag should stay small and steady
docker compose exec pg-1 psql -U postgres -c \
  "select slot_name, active, pg_size_pretty(
     pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) as behind
   from pg_replication_slots;"
```

A slot with `active = false` and a growing `behind` value means Connect is
not reading. That is the situation to fix quickly.

## Teardown

```bash
# stop the connector but keep its read position (normal restarts)
docker compose down
```

Removing this component for good takes one extra step, or PostgreSQL will
keep journal files for a reader that never returns:

```bash
# 1. delete the connector so it cannot recreate the slot
docker compose exec debezium-connect curl -X DELETE http://localhost:8083/connectors/nus-pg

# 2. drop the slot and the publication in PostgreSQL
docker compose exec pg-1 psql -U postgres -c "select pg_drop_replication_slot('nus_debezium');"
docker compose exec pg-1 psql -U postgres -c "drop publication if exists nus_pub;"
```
