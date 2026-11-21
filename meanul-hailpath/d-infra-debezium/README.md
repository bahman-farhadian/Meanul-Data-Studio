# d-infra-debezium — PostgreSQL CDC into Kafka

Kafka Connect with the Debezium PostgreSQL connector. WAL changes become
Avro messages on `cdc.<table>`. `cache-updater` applies those to Redis.

The worker can start as soon as Kafka and Schema Registry are up. Register
the connector only after `h-bootstrap` has created the OLTP tables.

## Connector

Definition: `connectors/hailpath-pg.json`. Password is injected at
`make cdc-register`. Slot `hailpath_debezium`, publication `hailpath_pub`,
`pgoutput`. Geometry column `hailpath.trips.route` is excluded. Topics
`cdc.*` are compacted, RF 1.

While the slot exists, Postgres keeps WAL the slot has not read. If this
piece is removed for good, delete the connector, then drop the slot and
publication.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-debezium` and `connector-register`. |
| `converter-prepare.sh` | Downloads Avro converter jars matching `AVRO_CONVERTER_VERSION`. |
| `connectors/hailpath-pg.json` | Connector config, no password. |
| `connectors/register.py` | PUT to Connect. |
| `.env.example` | Standalone copy of the settings this piece reads. |

Host port: `127.0.0.1:${PROXY_DEBEZIUM_PORT:-8083}` (loopback only). Image,
cgroup, and heap are in the root `.env`. Laptop `DEBEZIUM_MEM=1280m` with
heap `-Xmx384m`: RSS after the CDC cut is ~830 MiB, which filled a 640m
cgroup. Do not shrink the heap to chase a percentage.

## Verify (worker)

```bash
make verify-cdc
```

Until `cdc-register`, expect no `cdc.*` topics and no slot. The worker
itself answers `GET http://hailpath-debezium:8083/`.

## Teardown

```bash
docker compose down
```

To remove CDC for good (Postgres running):

```bash
docker compose exec hailpath-debezium curl -X DELETE http://localhost:8083/connectors/hailpath-pg
docker compose exec hailpath-postgres psql -U postgres -c "select pg_drop_replication_slot('hailpath_debezium');"
docker compose exec hailpath-postgres psql -U postgres -c "drop publication if exists hailpath_pub;"
```
