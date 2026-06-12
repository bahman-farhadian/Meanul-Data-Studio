# a-infra-postgres — PostgreSQL OLTP cluster + shared etcd

The system of truth for all transactional/operational state of the
not-uber-service stack: **three identical PostgreSQL nodes with automatic
failover, managed by [Patroni](https://patroni.readthedocs.io/)** (the
leader is elected — there is no fixed "primary" container), coordinated by
**nus-etcd, a 3-node TLS-secured etcd cluster**. Node naming starts at 1:
`etcd-1/2/3`, `pg-1/2/3`.

> **Naming:** the `nus-` prefix on shared resources (`nus-pg`, `nus-etcd`,
> `nus-backbone`, the `nus/` image namespace) is the acronym of
> **n**ot-**u**ber-**s**ervice.

Two clusters live in this component:

- **nus-pg** — Patroni/PostgreSQL, built on the pinned `postgres:18.4`
  image with **PostGIS** and **pgRouting** baked in (the extensions are
  created later by `h-bootstrap`'s migrations) and `wal_level = logical`
  preset for Debezium CDC (`d-infra-debezium`). All timestamps run in
  **UTC** (`TZ`, `timezone`, `log_timezone` all pinned).
- **nus-etcd** — a real 3-node etcd cluster (`etcd-1/2/3`) with **mutual
  TLS on both client and peer connections**. It is the **stack's shared
  key-value/DCS store**: any future component that needs etcd must reuse
  this cluster — never deploy a second one.

> **Base image note:** `select version();` reports
> `PostgreSQL 18.4 (Debian 18.4-1.pgdg13+1) ... compiled by gcc (Debian 14.2.0-19)`.
> The `pgdg13` means the package targets **Debian 13 "trixie" — the
> current stable release**; the `14.2.0` is the **GCC compiler version**,
> not a Debian release. The image is production-grade.

Nothing publishes ports to the host here — clients enter through the
stack's HAProxy pair defined in the root `docker-compose.yaml`
(`lb-a` / `lb-b`). For the full-stack, step-by-step runbook see
[`../README.md`](../README.md).

## TLS (etcd)

A one-shot `etcd-certgen` container generates everything into the
`etcd-certs` volume. It sits behind the `init` compose profile, so
`docker compose up` never starts it — it is run explicitly and **removes
itself on exit**:

```bash
docker compose run --rm etcd-certgen
```

What it produces (and never touches again — re-runs are no-ops):

- **CA** (`ca.crt`/`ca.key`), a **server/peer certificate**, and a
  **client certificate** — all valid **10 years** (3650 days);
- the server/peer certificate carries **SANs** for `etcd-1`, `etcd-2`,
  `etcd-3`, `localhost`, and `127.0.0.1`, so one certificate is valid on
  every node for both client-to-server and peer-to-peer connections;
- client certificate auth is **required** (`ETCD_CLIENT_CERT_AUTH=true`
  and the peer equivalent) — Patroni and the health checks authenticate
  with `client.crt`/`client.key`;
- the CA carries explicit `basicConstraints`/`keyUsage` extensions —
  Python 3.13 (which runs Patroni) verifies TLS in strict X.509 mode and
  rejects CAs without them.

## etcd cluster lifecycle — `new` vs `existing`

`etcd.env` (committed; it holds no secrets) carries the shared etcd
configuration. The critical knob is:

```
# FIRST bootstrap only
ETCD_INITIAL_CLUSTER_STATE=new
```

**After the first successful start, flip it to `existing`.**

**Why this matters:** etcd only reads the `ETCD_INITIAL_*` settings when a
member starts with an *empty* data dir. While the volumes exist, `new` is
harmlessly ignored — the danger is the day a member's volume is lost or
recreated. With `new` still set, that member would **bootstrap a brand-new
one-node cluster** instead of rejoining its peers — a split brain, with
Patroni potentially talking to two different "clusters". With `existing`,
the same member refuses to self-bootstrap and joins the running cluster.
The flip costs nothing and removes that failure mode permanently.

The exact procedure:

```bash
# 1. confirm the cluster actually bootstrapped (all three healthy)
docker compose exec etcd-1 etcdctl \
  --endpoints=https://etcd-1:2379,https://etcd-2:2379,https://etcd-3:2379 \
  --cacert=/certs/ca.crt --cert=/certs/client.crt --key=/certs/client.key \
  endpoint health

# 2. edit etcd.env: set ETCD_INITIAL_CLUSTER_STATE=existing
vim etcd.env

# 3. re-apply — compose recreates only the etcd containers (config changed);
#    the data volumes persist, so the members rejoin the existing cluster
docker compose up -d

# 4. verify the new value was picked up and the cluster is still healthy
docker compose logs etcd-1 | grep -i initial-cluster-state
docker compose exec pg-1 patronictl -c /etc/patroni/patroni.yml list
```

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
| `docker-compose.yaml` | `etcd-1/2/3` + `pg-1/2/3`, plus `etcd-certgen` behind the `init` profile (run with `docker compose run --rm`); included by the root compose. |
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
| `OPENSSL_IMAGE` | `alpine/openssl:3.5.6` | Image used by the cert one-shot. |
| `PATRONI_SCOPE` | `nus-pg` | Patroni cluster name. |
| `PATRONI_REST_USER` / `PATRONI_REST_PASSWORD` | `patroni` / — (required) | REST API credentials for unsafe endpoints. |
| `PG_SUPERUSER_PASSWORD` | — (required) | `postgres` superuser password. |
| `PG_REPLICATION_PASSWORD` | — (required) | `replicator` streaming-replication password. |

## Standalone quickstart

```bash
# one-time: the shared network every stack component joins
docker network create nus-backbone

# copy the settings template, then edit the passwords
cp .env.example .env

# one-shot TLS bootstrap (removes itself on exit; re-runs are no-ops)
docker compose run --rm etcd-certgen

docker compose up -d --build

# >>> after the first successful start:
#     edit etcd.env and set ETCD_INITIAL_CLUSTER_STATE=existing
```

Verify both clusters:

```bash
# etcd: all three members healthy over TLS
docker compose exec etcd-1 etcdctl \
  --endpoints=https://etcd-1:2379,https://etcd-2:2379,https://etcd-3:2379 \
  --cacert=/certs/ca.crt --cert=/certs/client.crt --key=/certs/client.key \
  endpoint health

# Patroni: member list with roles (Leader / Replica) and lag
docker compose exec pg-1 patronictl -c /etc/patroni/patroni.yml list
```

## Connecting as a DBA

**In the full stack, always connect through the HAProxy pair** — never to
a `pg-*` container directly: the leader moves on failover, and only the
proxies track it (via Patroni's REST API). This requires the **root**
`docker-compose.yaml` to be up (it owns `lb-a`/`lb-b`); see the runbook in
[`../README.md`](../README.md).

```bash
# writes — always lands on the current leader (lb-a, canonical ports)
psql -h localhost -p 5432 -U postgres

# reads — round-robins across the healthy replicas
psql -h localhost -p 5433 -U postgres

# writes via lb-b, the failover twin (alternate host ports)
psql -h localhost -p 15432 -U postgres

# reads via lb-b
psql -h localhost -p 15433 -U postgres
```

The password is `PG_SUPERUSER_PASSWORD` from your `.env`. From another
container on `nus-backbone`, use `-h lb-a` (or `lb-b`) instead of
`localhost`. HAProxy's live routing view: <http://localhost:8404/stats>.

**Reading the stats page — red rows are normal here.** The health checks
ask Patroni *which role a node holds*, not whether it is alive, so with a
healthy 3-node cluster the page always looks "partially down":

- `pg_write`: exactly **one** server UP (the current leader — it alone
  answers 200 on `/primary`); both replicas show DOWN with `L7STS/503`.
- `pg_read`: the **two replicas** UP (200 on `/replica`); the leader shows
  DOWN with `L7STS/503`.

A node DOWN in *both* backends is a real failure. After a switchover the
UP/DOWN pattern migrates to the new leader within a few check intervals.

For standalone testing of this component only (no lb running), exec into
a node directly:

```bash
docker compose exec pg-1 psql -U postgres -c "select version();"
```

## Failover demo

```bash
# planned switchover to a chosen replica (REST credentials required)
docker compose exec pg-1 patronictl -c /etc/patroni/patroni.yml switchover

# or kill the current leader (check `list` first; here assume pg-2 leads)
docker stop pg-2 && sleep 15
docker compose exec pg-1 patronictl -c /etc/patroni/patroni.yml list

# the stopped node rejoins as a replica (pg_rewind enabled)
docker start pg-2
```

HAProxy follows the promotion automatically via the Patroni REST checks —
clients on 5432 just reconnect and land on the new leader.

## Teardown

```bash
# keep data volumes
docker compose down

# destroy data + certs volumes too
# (afterwards, set ETCD_INITIAL_CLUSTER_STATE back to 'new')
docker compose down -v
```
