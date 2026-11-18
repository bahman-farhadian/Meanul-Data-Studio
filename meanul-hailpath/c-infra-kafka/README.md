# c-infra-kafka — one broker, Schema Registry, and ksqlDB

One Kafka process in KRaft mode: `hailpath-kafka`. It is the broker and
the controller. There is no ZooKeeper.

Schema Registry holds Avro schemas. ksqlDB is the SQL reader. Debezium
writes `cdc.*`. Services publish the declared topics.

Declared topics use replication factor 1 and `min.insync.replicas=1`.
`auto.create.topics.enable` is false. Default retention is 48 hours.
ClickHouse is where history lives.

On `hailpath-net`: `hailpath-kafka:9092`. Host: this container on **9094**
(`KAFKA_ADVERTISED_HOST`). ksqlDB: `PROXY_KSQLDB_PORT` (default 8089) onto
8088. Schema Registry: 8081 on the network only.

Image pins, heaps, and memory are in the root `.env`.

## ksqlDB

DBeaver speaks the REST API. Login is `KSQLDB_ADMIN_USER` /
`KSQLDB_ADMIN_PASSWORD` (HTTP Basic). Pull queries scan the topic — send a
`LIMIT`.

`make verify-ksqldb` checks streams/tables in `ksql/*.sql`, persistent
queries RUNNING, and pull-stream / table-scan / `earliest` offset.

## Avro

One `.avsc` per declared topic. Compatibility `backward`. `make schemas`
registers them. `make verify-schemas` compares Registry subjects to those
files.

## Topics

`topics/topics.tsv`, created by `kafka-topics-init` at RF 1. `cdc.*` are
not in the file; Debezium creates those.

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
| `docker-compose.yaml` | Broker, Schema Registry, ksqlDB, init one-shots. |
| `topics/topics.tsv` | Name, partitions, retention, key. |
| `topics/create-topics.sh` | Creates missing topics. RF 1. |
| `schemas/*.avsc` | Avro for each declared topic. |
| `ksql/*.sql` | Streams and tables for `make ksql-ddl`. |
| `.env.example` | Standalone copy of the settings this piece reads. |

`make up` calls `ksqldb-secrets`, `topics`, `schemas`, and `ksql-ddl`.

## Verify

```bash
make verify-kafka
make verify-schemas
make verify-ksqldb
```

## Teardown

```bash
docker compose down
docker compose down -v
```
