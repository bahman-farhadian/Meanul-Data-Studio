# a-infra-postgres — PostgreSQL OLTP cluster + shared etcd

The system of truth for all transactional/operational state of the
not-uber-service stack: **three identical PostgreSQL nodes with automatic
failover, managed by [Patroni](https://patroni.readthedocs.io/)** (the
leader is elected — there is no fixed "primary" container), coordinated by
**nus-etcd, a 3-node TLS-secured etcd cluster**.

> **Naming:** the `nus-` prefix on shared resources (`nus-pg`, `nus-etcd`,
> `nus-backbone`, the `nus/` image namespace) is the acronym of
> **n**ot-**u**ber-**s**ervice.

Two clusters live in this component:

- **nus-pg** — Patroni/PostgreSQL, built on the pinned `postgres:18.4`
  image with **PostGIS** and **pgRouting** baked in (the extensions are
  created later by `h-bootstrap`'s migrations) and `wal_level = logical`
  preset for Debezium CDC (`d-infra-debezium`). All timestamps run in
  **UTC** (`TZ`, `timezone`, `log_timezone` all pinned).
- **nus-etcd** — a real 3-node etcd cluster (`etcd-0/1/2`) with **mutual
  TLS on both client and peer connections**. It is the **stack's shared
  key-value/DCS store**: any future component that needs etcd must reuse
  this cluster — never deploy a second one.

Nothing publishes ports to the host here — clients enter through the
stack's HAProxy pair defined in the root `docker-compose.yaml`
(`lb-a` / `lb-b`): port **5432** routes to the current leader (writes),
port **5433** round-robins the healthy replicas (reads), both driven by
Patroni's REST API health endpoints.

## TLS (etcd)

A one-shot `etcd-certgen` container generates everything into the
`etcd-certs` volume on first start and never touches it again:

- **CA** (`ca.crt`/`ca.key`), a **server/peer certificate**, and a
  **client certificate** — all valid **10 years** (3650 days);
- the server/peer certificate carries **SANs** for `etcd-0`, `etcd-1`,
  `etcd-2`, `localhost`, and `127.0.0.1`, so one certificate is valid on
  every node for both client-to-server and peer-to-peer connections;
- client certificate auth is **required** (`ETCD_CLIENT_CERT_AUTH=true`
  and the peer equivalent) — Patroni and the health checks authenticate
  with `client.crt`/`client.key`.

## etcd cluster lifecycle — `new` vs `existing`

`etcd.env` (committed; it holds no secrets) carries the shared etcd
configuration. The critical knob is:

```
ETCD_INITIAL_CLUSTER_STATE=new        # FIRST bootstrap only
```

**After the first successful start, flip it to `existing`.** `new` is only
valid while the cluster is being formed from empty data volumes; once
`etcd-data-*` exist, members rejoin an *existing* cluster on restart.

## Patroni REST API

Each node exposes Patroni's REST API on port 8008:

- **Authenticated** (via `PATRONI_REST_USER`/`PATRONI_REST_PASSWORD`):
  the unsafe endpoints — switchover, failover, restart, reload, config.
- **Open by design**: the read-only monitoring GETs (`/primary`,
  `/replica`, `/health`) — the lb-a/lb-b HAProxy checks rely on them to
  route writes to the leader and reads to replicas across failovers.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `etcd-certgen` (one-shot TLS bootstrap) + `etcd-0/1/2` + `pg-0/1/2`; included by the root compose. |
| `Dockerfile` | `postgres:18.4` + PostGIS + pgRouting + Patroni (own venv). |
| `patroni.yml` | Shared Patroni config (etcd3 TLS endpoints, REST API, DCS settings, initdb, pg_hba, UTC timezone). Per-node values/secrets injected as `PATRONI_*` env vars. |
| `etcd.env` | Shared etcd cluster settings incl. TLS paths and `ETCD_INITIAL_CLUSTER_STATE` (committed — no secrets). |
| `certs/gen-certs.sh` | Idempotent 10-year CA/server/client cert generation with SANs. |
| `.env.example` | Template for the untracked `.env` (image pins, TZ, passwords). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone — the whole stack runs UTC. |
| `PG_IMAGE` | `postgres:18.4` | Pinned base image for the node build. |
| `ETCD_IMAGE` | `quay.io/coreos/etcd:v3.6.12` | etcd image. |
| `OPENSSL_IMAGE` | `alpine/openssl:3.3.2` | Image used by the cert one-shot. |
| `PATRONI_SCOPE` | `nus-pg` | Patroni cluster name. |
| `PATRONI_REST_USER` / `PATRONI_REST_PASSWORD` | `patroni` / — (required) | REST API credentials for unsafe endpoints. |
| `PG_SUPERUSER_PASSWORD` | — (required) | `postgres` superuser password. |
| `PG_REPLICATION_PASSWORD` | — (required) | `replicator` streaming-replication password. |

## Standalone quickstart

```bash
# one-time: the shared network every stack component joins
docker network create nus-backbone

cp .env.example .env        # then edit the passwords
docker compose up -d --build

# >>> after the first successful start:
#     edit etcd.env and set ETCD_INITIAL_CLUSTER_STATE=existing
```

Verify both clusters:

```bash
# etcd: all three members healthy over TLS
docker compose exec etcd-0 etcdctl \
  --endpoints=https://etcd-0:2379,https://etcd-1:2379,https://etcd-2:2379 \
  --cacert=/certs/ca.crt --cert=/certs/client.crt --key=/certs/client.key \
  endpoint health

# Patroni: member list with roles (Leader / Replica) and lag
docker compose exec pg-0 patronictl -c /etc/patroni/patroni.yml list

# connect directly to a node (standalone testing only — in the stack,
# always go through lb-a/lb-b)
docker compose exec pg-0 psql -U postgres -c "select version();"
```

Through the stack's entry tier (root compose running):

```bash
psql -h localhost -p 5432 -U postgres   # writes -> current leader
psql -h localhost -p 5433 -U postgres   # reads  -> replica pool
```

## Failover demo

```bash
# planned switchover to a chosen replica (REST credentials required)
docker compose exec pg-0 patronictl -c /etc/patroni/patroni.yml switchover

# or kill the current leader and watch Patroni promote a replica
docker stop pg-0 && sleep 15
docker compose exec pg-1 patronictl -c /etc/patroni/patroni.yml list
docker start pg-0   # rejoins as a replica (pg_rewind enabled)
```

HAProxy follows the promotion automatically via the Patroni REST checks —
clients on 5432 just reconnect and land on the new leader.

## Teardown

```bash
docker compose down          # keep data volumes
docker compose down -v       # destroy data + certs volumes too
# after a full -v teardown, set ETCD_INITIAL_CLUSTER_STATE back to 'new'
```
