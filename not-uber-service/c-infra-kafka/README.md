# c-infra-kafka — the streaming backbone

Three Kafka brokers (`kafka-1/2/3`) in **KRaft mode** plus the
**Schema Registry**. Every event the stack produces travels through here:
driver and rider positions, ride requests, trip status changes, city
hotspot scores, and the `cdc.*` change stream Debezium reads out of
PostgreSQL.

**KRaft mode** means the brokers manage themselves. In older Kafka a
separate ZooKeeper cluster decided who was in charge; now each of the three
nodes is a broker *and* a controller, and they elect a leader among
themselves. Three nodes, one less cluster to run.

> **Naming:** the `nus-` prefix on shared resources (`nus-kafka-data-*`,
> `nus-backbone`) is the acronym of **n**ot-**u**ber-**s**ervice.

Nothing publishes ports to the host, and Kafka is **not** placed behind the
`lb-a`/`lb-b` HAProxy pair. A Kafka client asks any broker for the cluster
layout and then talks to the exact broker that owns each partition, so a TCP
proxy in front would break that routing rather than help it.

## Why binary Avro

Messages are not JSON. Each message carries a small binary body plus the
**id of its schema**, and the Schema Registry says what that id means. Two
things follow:

- messages are much smaller, because field names are not repeated in every
  single record;
- a schema cannot change in a way that breaks existing readers. The registry
  is set to `backward` compatibility, so adding an optional field is
  accepted and removing a field somebody still reads is refused at
  registration time.

Producers register their schema at startup and consumers resolve schemas by
the id inside each message. The schema files themselves live in
[`schemas/`](schemas/) and are the single source of truth for the whole
stack; the Python services copy them in at build time.

Binary does not mean unreadable — see
[Reading topics by hand](#reading-topics-by-hand).

## Safety settings

| Setting | Value | What it buys |
| --- | --- | --- |
| replication factor | 3 | every message exists on all three brokers |
| `min.insync.replicas` | 2 | a write is accepted only while two brokers still have it, so one broker can die without data loss |
| `auto.create.topics.enable` | `false` | a typo in a topic name fails loudly instead of quietly creating a new topic |
| retention | 48 h | long enough to fix and replay a consumer; ClickHouse is where history lives |

With replication 3 and `min.insync.replicas` 2, losing **one** broker costs
nothing. Losing **two** stops writes on purpose: Kafka refuses rather than
accepting messages it cannot keep safe.

## Topics

Topics are declared in [`topics/topics.tsv`](topics/topics.tsv) and created
by the `kafka-topics-init` one-shot. The two location topics get 6
partitions because they carry by far the most traffic; the rest get 3, one
per broker. Each topic has a **key** so that everything about one driver,
rider, or trip lands on the same partition and therefore stays in order.

To add a topic: add a line to `topics.tsv` and run the one-shot again. It
skips topics that already exist, so re-running is always safe. Changing an
existing topic is deliberately not automated — Kafka cannot shrink the
partition count of a live topic, so that is a decision, not a script.

The `cdc.*` topics are not listed there: Debezium creates one per
PostgreSQL table (see [`../d-infra-debezium`](../d-infra-debezium)).

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `kafka-1/2/3`, `schema-registry`, and the two one-shots (`kafka-dirs`, `kafka-topics-init`) behind the `init` profile. |
| `topics/topics.tsv` | The topic list: name, partitions, message key, and what it is for. |
| `topics/create-topics.sh` | Reads that file and creates anything missing. Safe to re-run. |
| `schemas/*.avsc` | The Avro schema of each topic, in plain JSON with a `doc` note on every field. |
| `.env.example` | Template for the untracked `.env` (image pins, cluster id, retention). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone — the whole stack runs UTC. |
| `KAFKA_IMAGE` | `apache/kafka:4.3.1` | Broker image, also used by the one-shots. |
| `SCHEMA_REGISTRY_IMAGE` | `confluentinc/cp-schema-registry:8.3.1` | Schema Registry image. |
| `BUSYBOX_IMAGE` | `busybox:1.38.0` | Tiny image used by the `kafka-dirs` one-shot. |
| `KAFKA_CLUSTER_ID` | (in `.env.example`) | Identity of the cluster. Same on all brokers, never changed after the first format. |
| `KAFKA_UID` / `KAFKA_GID` | `1000` / `1000` | The user inside the Kafka image; `kafka-dirs` hands the volumes to it. |
| `KAFKA_RETENTION_HOURS` | `48` | How long messages are kept. |

## Standalone quickstart

> Standalone runs use the `c-infra-kafka` project scope. The `nus-*` volumes
> are shared with the full stack, but Docker labels each volume with the
> project that created it, so mixing scopes prints a harmless
> `volume ... was created for project ...` warning. For stack assembly, run
> everything through the root compose per the [runbook](../README.md).

```bash
# one-time: the shared network every stack component joins
docker network create nus-backbone

cp .env.example .env

# one-shot BEFORE the first start: a new Docker volume belongs to root, and
# the broker does not run as root, so hand the volumes over first
docker compose run --rm kafka-dirs

docker compose up -d

# one-shot AFTER the brokers are healthy: create the topics
docker compose run --rm kafka-topics-init
```

## First bring-up checks

Two things depend on how the image is built rather than on this repository,
so confirm them once on the first deployment:

```bash
# which user the broker runs as - feed the result back into KAFKA_UID/GID
docker run --rm apache/kafka:4.3.1 id

# the brokers should log that they formatted storage with the cluster id
# from .env, and then elect a controller
docker compose logs kafka-1 | head -50
```

## Verify

```bash
# the three brokers, as the cluster itself sees them
docker compose exec kafka-1 /opt/kafka/bin/kafka-broker-api-versions.sh \
  --bootstrap-server kafka-1:9092

# the controller quorum: one leader, two followers, nobody lagging
docker compose exec kafka-1 /opt/kafka/bin/kafka-metadata-quorum.sh \
  --bootstrap-server kafka-1:9092 describe --status

# every topic, with its partitions and where the copies live
docker compose exec kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka-1:9092 --describe

# the Schema Registry answers, and lists what has been registered so far
docker compose exec schema-registry curl -s http://localhost:8081/subjects
```

In a healthy `--describe` output every partition shows three replicas and
three in-sync replicas (`Isr`). A partition whose `Isr` is smaller than its
replica list is a broker that has fallen behind or died.

## Reading topics by hand

The messages are binary, but nothing about them is hidden.

```bash
# decoded to JSON as it arrives, from any container on nus-backbone
kcat -b kafka-1:9092 -t driver_location -C -e \
     -s value=avro -r http://schema-registry:8081

# the same, using the tool that ships inside the Schema Registry image
docker compose exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --property schema.registry.url=http://schema-registry:8081 \
  --topic driver_location --from-beginning --max-messages 5

# what the registry thinks a topic looks like right now
docker compose exec schema-registry \
  curl -s http://localhost:8081/subjects/driver_location-value/versions/latest
```

`kcat` must be a build with Avro support for `-s value=avro` to work; the
`kafka-avro-console-consumer` above always works because it comes from the
registry image itself.

For SQL over a live topic, ClickHouse can read Kafka directly with its Kafka
table engine (`format = 'AvroConfluent'` plus
`format_avro_schema_registry_url`), and for SQL over the full history
everything is in ClickHouse anyway, put there by `clickhouse-sink`.

## Failover demo

```bash
# who leads which partition right now
docker compose exec kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka-1:9092 --describe --topic trip_lifecycle

# stop a broker and look again: leadership has moved to the other two,
# and the stopped broker has dropped out of Isr
docker stop kafka-2
docker compose exec kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka-1:9092 --describe --topic trip_lifecycle

# bring it back: it catches up and rejoins Isr on its own
docker start kafka-2
```

Producers and consumers are expected to ride through this without being
restarted.

## Teardown

```bash
# keep the message log
docker compose down

# destroy the message log as well. The next start formats fresh volumes, so
# re-run kafka-dirs first and kafka-topics-init afterwards.
docker compose down -v
```
