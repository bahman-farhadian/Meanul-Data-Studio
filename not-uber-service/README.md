# not-uber-service — stack runbook

How to bring the full stack up with the root
[`docker-compose.yaml`](docker-compose.yaml), piece by piece, in the
alphabetic build order (`a-` ... `n-`). **This file covers assembly order
only** — what each component is and how to verify it lives in that
component's own README. Architecture: the repository's main
[README](../README.md).

All commands run from this directory (`not-uber-service/`). These rules
apply to every piece:

- **One-shot containers** (cert generation and the like) run via
  `docker compose run --rm <name>` from this directory, against the root
  compose file — they do their job and remove themselves. Running them
  against a component's own compose file works too, but the volumes they
  create get labelled with that component's project name, and every later
  root `up` warns `volume ... was created for project "a-infra-postgres"`
  (harmless, but noisy — keep one project scope and it never appears).
- **Everything else comes up through the root compose file only** —
  never `up` a component's own compose file when assembling the stack
  (volumes carry fixed `nus-*` names, so the one-shot output is shared
  either way).
- The stack targets a dedicated Docker server with 20 CPU cores and 120 GB
  RAM. Per-container CPU/memory limits and the no-swap policy are declared
  directly in the compose files; see the main README, section 2.9.

## 0. One-time groundwork

```bash
# shared network ("nus" = not-uber-service)
docker network create nus-backbone

# stack-wide settings (lb-a/lb-b entry tier)
cp .env.example .env
```

## 1. Piece a — a-infra-postgres (+ lb-a/lb-b entry tier)

```bash
# settings — EDIT THE PASSWORDS
cp a-infra-postgres/.env.example a-infra-postgres/.env

# one-shot: generate the nus-etcd TLS certificates (removes itself on exit)
docker compose run --rm etcd-certgen

# bring everything assembled so far up — ALWAYS via the root compose file
docker compose up -d --build
```

Verify it: [a-infra-postgres/README.md](a-infra-postgres/README.md)
(etcd health, `patronictl list`, connecting as a DBA, reading the HAProxy
stats page at <http://localhost:8404/stats>).

**Post-bootstrap (once, after the first successful start):** flip the etcd
cluster state from `new` to `existing`:

```bash
# set ETCD_INITIAL_CLUSTER_STATE=existing
vim a-infra-postgres/etcd.env

# re-apply: recreates only the etcd containers; data persists
docker compose up -d
```

This flip is **local operational state — never commit it**; the repository
keeps `new` so a fresh clone can bootstrap from empty volumes.

Why this flip matters (split-brain protection when a volume is ever lost):
[a-infra-postgres/README.md](a-infra-postgres/README.md#etcd-cluster-lifecycle--new-vs-existing).

## 2. Piece b — b-infra-redis

```bash
# settings — EDIT THE PASSWORD
cp b-infra-redis/.env.example b-infra-redis/.env

# bring everything assembled so far up — ALWAYS via the root compose file
docker compose up -d --build
```

No one-shots and no post-bootstrap step here: the Sentinel set elects its
own primary, and each node materialises its live config on first start.

Verify it: [b-infra-redis/README.md](b-infra-redis/README.md) (node roles,
what Sentinel believes, `ckquorum`, a write/read across the replica).

**Know before you edit:** the committed `redis.conf` / `sentinel.conf` are
templates copied onto each node's volume on first start — Sentinel owns the
live files after that. Changing a template or `REDIS_PASSWORD` later has no
effect until those volumes are dropped
([why](b-infra-redis/README.md#config-file-lifecycle--the-one-thing-to-know)).

## 3. Piece c — c-infra-kafka

```bash
# settings (no secrets here; the cluster id is already filled in)
cp c-infra-kafka/.env.example c-infra-kafka/.env

# one-shot BEFORE the first start: a new Docker volume belongs to root and
# the broker does not run as root, so hand the volumes over first
docker compose run --rm kafka-dirs

# bring everything assembled so far up — ALWAYS via the root compose file
docker compose up -d --build

# one-shot AFTER the brokers are healthy: create the topics
docker compose run --rm kafka-topics-init
```

Verify it: [c-infra-kafka/README.md](c-infra-kafka/README.md) (broker list,
controller quorum, `--describe` showing three in-sync replicas per
partition, and how to read a binary Avro topic as JSON).

**Adding a topic later:** add its line to
[c-infra-kafka/topics/topics.tsv](c-infra-kafka/topics/topics.tsv) and run
`docker compose run --rm kafka-topics-init` again. Existing topics are left
untouched.

## 4. Piece d — d-infra-debezium

```bash
# settings — the password must match PG_SUPERUSER_PASSWORD from piece a
cp d-infra-debezium/.env.example d-infra-debezium/.env

# bring everything assembled so far up — ALWAYS via the root compose file
docker compose up -d --build
```

**Do not register the connector yet.** It names the tables it follows, so
those tables must exist first. The registration one-shot belongs to piece h,
right after `h-bootstrap` has finished:

```bash
# LATER, after h-bootstrap has created and seeded the tables
docker compose run --rm connector-register
```

Verify it: [d-infra-debezium/README.md](d-infra-debezium/README.md)
(connector status, the `cdc.*` topics, a change travelling from a `psql`
update to the topic, and the replication-slot health check).

**Watch the replication slot.** While the slot exists, PostgreSQL keeps
every journal file Debezium has not read. If this component is removed for
good, drop the slot as well — the teardown section of its README shows how.

## 5. Piece e — e-infra-clickhouse

```bash
# settings — EDIT THE PASSWORD
cp e-infra-clickhouse/.env.example e-infra-clickhouse/.env

# bring everything assembled so far up — ALWAYS via the root compose file
docker compose up -d --build

# one-shot AFTER the four nodes are healthy: create the tables
docker compose run --rm ch-ddl-init
```

This piece also opens the ClickHouse routes on the entry tier: `lb-a` now
publishes 8123 (HTTP) and 9000 (native), `lb-b` publishes 18123 and 19000.

Verify it: [e-infra-clickhouse/README.md](e-infra-clickhouse/README.md)
(cluster members, Keeper leader, the tables, replication delay, and a write
on one node read back through another).

**Adding a table later:** add a numbered file to
[e-infra-clickhouse/ddl/](e-infra-clickhouse/ddl/) and run
`docker compose run --rm ch-ddl-init` again.

## 6. Next pieces — f ... n

Added here as each component lands, in the same shape as pieces a to e:
copy its `.env.example` if it has one, activate its `include:` entry in the
root [`docker-compose.yaml`](docker-compose.yaml), run its one-shot
containers (if any) with `docker compose run --rm <name>`,
add its resource limits to its compose file, run the root `up` from piece
a, then its post-bootstrap commands (if any) — verification always per the
component README.

## Teardown

```bash
# whole stack, keep data volumes
docker compose down

# whole stack, destroy data volumes
docker compose down -v

# only if removing the stack for good
docker network rm nus-backbone
```

After a `-v` teardown, repeat each piece's one-shot and post-bootstrap
steps on the next bring-up (for piece a: regenerate certs, set
`ETCD_INITIAL_CLUSTER_STATE` back to `new` first, flip again after).
