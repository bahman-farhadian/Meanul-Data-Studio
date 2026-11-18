# c-infra-kafka — one broker, Schema Registry, and ksqlDB

One Kafka process, `hailpath-kafka` / `hailpath-kafka`, in KRaft mode. It is the
broker and the controller. There is no ZooKeeper.

Schema Registry holds the Avro schemas. ksqlDB is how a SQL client reads
this broker. Debezium writes `cdc.*` here, and the services publish the
declared topics.

A second copy of a topic on this same host would not survive the host
dying, so declared topics use replication factor 1 and
`min.insync.replicas=1`. Partitions stay. A key still orders on one
partition. `auto.create.topics.enable` is false. Default retention is
48 hours. ClickHouse is where history lives.

Containers on `hailpath-net` use `hailpath-kafka:9092`. From the host,
this container publishes **9094** (`KAFKA_ADVERTISED_HOST:9094`). ksqlDB
is published as `PROXY_KSQLDB_PORT` (default 8089) onto container 8088.
Schema Registry stays on the network at 8081.

Memory is sized in the root `.env` with the heap. Laptop: `KAFKA_MEM=1280m`
/ heap 768m, Schema Registry 448m / 256m, ksqlDB 768m / 384m. Deploy:
Kafka `5.5g` / 3500m, Schema Registry `1g` / 512m, ksqlDB `1.5g` / 1g.

`make init` fills `KAFKA_ADVERTISED_HOST` from this machine's default-route
interface. Override it if the guess is wrong.

## ksqlDB

ksqlDB is the SQL reader. DBeaver speaks its REST API, not the raw
broker protocol.

| | host |
| --- | --- |
| ksqlDB | `<host>:8089` |

Login is `KSQLDB_ADMIN_USER` / `KSQLDB_ADMIN_PASSWORD` (HTTP Basic).
That lock is the REST API. Kafka itself has no authentication in this
stack.

A pull query over a stream scans the topic. Send a `LIMIT`:

```sql
SELECT * FROM DRIVER_LOCATION LIMIT 200;
SELECT * FROM TRIP_LIFECYCLE WHERE trip_key = 'trp-…' LIMIT 200;
```

`make verify-ksqldb` checks that every stream and table in `ksql/*.sql`
is registered, that persistent queries are RUNNING, and that
`ksql.query.pull.stream.enabled` is `true`,
`ksql.query.pull.table.scan.enabled` is `true`, and
`ksql.streams.auto.offset.reset` is `earliest`. Derived tables set
`REPLICAS=1`, because ksqlDB does not inherit the broker default.

## Avro

Each message carries a schema id. Schema Registry resolves it.
Compatibility is `backward`. The files in `schemas/` are the source.
One `.avsc` per declared topic.

`make schemas` registers them all, before anything produces. `make
verify-schemas` reads them back and compares namespace, record name and
field list with the file on disk, so a Registry still holding an older
copy fails instead of merely answering. Both run inside `schema-init`:
port 8081 is not published on the host, and the Registry
image ships neither curl nor wget.

```bash
docker compose exec hailpath-schema-registry kafka-avro-console-consumer \
  --bootstrap-server hailpath-kafka:9092 \
  --property schema.registry.url=http://hailpath-schema-registry:8081 \
  --topic driver_location --from-beginning --max-messages 5
```

## Topics

Declared in `topics/topics.tsv` and created by `kafka-topics-init` with
`--replication-factor 1` and `min.insync.replicas=1`. The script skips
topics that already exist. `cdc.*` are not in the file. Debezium creates
them, also at replication factor 1.

| Topic | Partitions | Key |
| --- | --- | --- |
| `driver_location` | 12 | `driver_id` |
| `rider_location` | 12 | `rider_id` |
| `trip_requests` | 6 | `trip_id` |
| `trip_lifecycle` | 6 | `trip_id` |
| `dispatch_offers` | 6 | `trip_id` |
| `city_hotspots` | 3 | `zone_id` |
| `segment_traffic_updates` | 3 | `zone_id` |

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-kafka`, `schema-registry`, `ksqldb-server`, and the init one-shots. |
| `topics/topics.tsv` | Name, partitions, retention, key. |
| `topics/create-topics.sh` | Creates anything missing. Replication factor 1. |
| `schemas/*.avsc` | Avro schema of each declared topic. |
| `ksql/*.sql` | Streams and tables `make ksql-ddl` registers. |
| `.env.example` | Image pins, cluster id, retention, ksqlDB login. |

`make up` calls `ksqldb-secrets`, `topics`, `schemas`, and `ksql-ddl`.
`volume-perms` hands `hailpath-kafka-data` to `KAFKA_UID`. The `kafka-dirs`
compose service does the same chown for a standalone start of this
directory.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAFKA_IMAGE` | `apache/kafka:4.3.2` | Broker image. |
| `SCHEMA_REGISTRY_IMAGE` | `confluentinc/cp-schema-registry:8.3.3` | Schema Registry. |
| `KSQLDB_IMAGE` | `confluentinc/cp-ksqldb-server:8.3.3` | ksqlDB server. |
| `KSQLDB_ADMIN_USER` | `admin` | REST login. |
| `KAFKA_CLUSTER_ID` | in `.env.example` | Storage identity. Do not change it after the first format. |
| `KAFKA_RETENTION_HOURS` | `48` | Default retention. |
| `KAFKA_ADVERTISED_HOST` | required | Hostname advertised on port 9094. |

## Verify

```bash
make verify-kafka
make verify-schemas
make verify-ksqldb
```

`verify-kafka` describes topics on `hailpath-kafka:9092`. Every partition
has one replica, and that replica is in sync. `verify-schemas` checks the
seven subjects this repository declares; the `cdc.*`, heartbeat and
ksqlDB subjects are counted and otherwise left alone.

## Teardown

```bash
docker compose down
docker compose down -v
```
