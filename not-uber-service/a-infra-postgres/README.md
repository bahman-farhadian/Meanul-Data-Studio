# a-infra-postgres — PostgreSQL OLTP cluster

The system of truth for all transactional/operational state of the
not-uber-service stack: **one primary + two streaming replicas with
automatic failover, managed by [Patroni](https://patroni.readthedocs.io/)**
against a single-node **etcd** DCS. Built on the pinned `postgres:18.4`
image with **PostGIS** and **pgRouting** baked in (the extensions are
created later by `h-bootstrap`'s migrations) and `wal_level = logical`
preset for Debezium CDC (`d-infra-debezium`).

All three `pg-*` nodes are identical; Patroni elects the leader. Nothing
publishes ports to the host here — clients enter through the stack's
HAProxy pair defined in the root `docker-compose.yaml` (`lb-a` / `lb-b`):
port **5432** routes to the current primary (writes), port **5433**
round-robins the healthy replicas (reads), both driven by Patroni's REST
API health endpoints.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | etcd + the three Patroni nodes (`pg-0`, `pg-1`, `pg-2`); included by the root compose. |
| `Dockerfile` | `postgres:18.4` + PostGIS + pgRouting + Patroni (in its own venv). |
| `patroni.yml` | Shared Patroni config (DCS settings, initdb, pg_hba, PostgreSQL parameters). Per-node values and secrets are injected as `PATRONI_*` env vars from the compose file. |
| `.env.example` | Template for the untracked `.env` (image tags, scope, passwords). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `PG_IMAGE` | `postgres:18.4` | Pinned base image for the node build. |
| `ETCD_IMAGE` | `quay.io/coreos/etcd:v3.6.5` | etcd DCS image. |
| `PATRONI_SCOPE` | `nus-pg` | Patroni cluster name. |
| `PG_SUPERUSER_PASSWORD` | — (required) | `postgres` superuser password. |
| `PG_REPLICATION_PASSWORD` | — (required) | `replicator` streaming-replication password. |

## Standalone quickstart

```bash
# one-time: the shared network every stack component joins
docker network create nus-backbone

cp .env.example .env        # then edit the passwords
docker compose up -d --build
```

Verify the cluster:

```bash
# member list with roles (Leader / Replica) and lag
docker compose exec pg-0 patronictl -c /etc/patroni/patroni.yml list

# connect directly to a node (standalone testing only — in the stack,
# always go through lb-a/lb-b)
docker compose exec pg-0 psql -U postgres -c "select version();"
```

Through the stack's entry tier (root compose running):

```bash
psql -h localhost -p 5432 -U postgres   # writes -> current primary
psql -h localhost -p 5433 -U postgres   # reads  -> replica pool
```

## Failover demo

```bash
# planned switchover to a chosen replica
docker compose exec pg-0 patronictl -c /etc/patroni/patroni.yml switchover

# or kill the current leader and watch Patroni promote a replica
docker stop pg-0 && sleep 15
docker compose exec pg-1 patronictl -c /etc/patroni/patroni.yml list
docker start pg-0   # rejoins as a replica (pg_rewind enabled)
```

HAProxy follows the promotion automatically via the Patroni REST checks —
clients on 5432 just reconnect and land on the new primary.

## Teardown

```bash
docker compose down          # keep data volumes
docker compose down -v       # destroy data volumes too
```
