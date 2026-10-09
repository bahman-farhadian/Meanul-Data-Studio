# c-infra-kafka — one broker, Schema Registry, and ksqlDB

One Kafka process, `kafka-1` / `nus-kafka-1`, in KRaft mode. It is the
broker and the controller. There is no ZooKeeper.

Schema Registry holds the Avro schemas. ksqlDB is how a SQL client reads
this broker. Debezium writes `cdc.*` here, and the services publish the
declared topics.

A second copy of a topic on this same host would not survive the host
dying, so declared topics use replication factor 1 and
`min.insync.replicas=1`. Partitions stay. A key still orders on one
partition. `auto.create.topics.enable` is false. Default retention is
48 hours. ClickHouse is where history lives.

Containers on `nus-backbone` use `nus-kafka-1:9092`. From the host,
HAProxy publishes the one broker on two advertised addresses:

| Address | Port |
| --- | --- |
| `KAFKA_ADVERTISED_HOST_A` | `9094` |
| `KAFKA_ADVERTISED_HOST_B` | `9097` |

`make init` fills those hostnames from this machine's interfaces. A
client bootstraps on either port. Both are `nus-kafka-1`.

## ksqlDB

ksqlDB is the SQL reader. DBeaver speaks its REST API, not the raw
broker protocol.

| | lb-a | lb-b |
| --- | --- | --- |
| ksqlDB | `<host>:8089` | `<host>:18089` |

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

```bash
docker compose exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server nus-kafka-1:9092 \
  --property schema.registry.url=http://nus-schema-registry:8081 \
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
| `docker-compose.yaml` | `kafka-1`, `schema-registry`, `ksqldb-server`, and the init one-shots. |
| `topics/topics.tsv` | Name, partitions, retention, key. |
| `topics/create-topics.sh` | Creates anything missing. Replication factor 1. |
| `schemas/*.avsc` | Avro schema of each declared topic. |
| `ksql/*.sql` | Streams and tables `make ksql-ddl` registers. |
| `.env.example` | Image pins, cluster id, retention, ksqlDB login. |

`make up` calls `ksqldb-secrets`, `topics`, `schemas`, and `ksql-ddl`.
`volume-perms` hands `nus-kafka-data-1` to `KAFKA_UID`. The `kafka-dirs`
compose service does the same chown for a standalone start of this
directory.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAFKA_IMAGE` | `apache/kafka:4.3.1` | Broker image. |
| `SCHEMA_REGISTRY_IMAGE` | `confluentinc/cp-schema-registry:8.3.1` | Schema Registry. |
| `KSQLDB_IMAGE` | `confluentinc/cp-ksqldb-server:8.3.1` | ksqlDB server. |
| `KSQLDB_ADMIN_USER` | `admin` | REST login. |
| `KAFKA_CLUSTER_ID` | in `.env.example` | Storage identity. Do not change it after the first format. |
| `KAFKA_RETENTION_HOURS` | `48` | Default retention. |
| `KAFKA_ADVERTISED_HOST_A` / `_B` | required | Hostnames advertised on ports 9094 and 9097. |

## Verify

```bash
make verify-kafka
make verify-ksqldb
```

`verify-kafka` describes topics on `nus-kafka-1:9092`. Every partition
has one replica, and that replica is in sync.

## Teardown

```bash
docker compose down
docker compose down -v
```
