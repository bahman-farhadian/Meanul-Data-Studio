# not-uber-service — running the stack

Step-by-step runbook for bringing the full stack up with the root
[`docker-compose.yaml`](docker-compose.yaml). The stack assembles like a
puzzle: shared groundwork first, then each component piece by piece, in
the alphabetic build order (`a-` ... `n-`). One-shot containers are always
run explicitly with `docker compose run --rm` (they remove themselves) and
are never part of `up`.

All commands below are run from this directory
(`not-uber-service/`). The architecture itself is documented in the
repository's main [README](../README.md).

## 0. One-time groundwork

```bash
# the shared Docker network every component joins
# ("nus" = not-uber-service)
docker network create nus-backbone

# stack-wide settings for the lb-a/lb-b entry tier (untracked)
cp .env.example .env
```

## 1. Piece a — PostgreSQL OLTP cluster (a-infra-postgres)

```bash
# component settings — EDIT THE PASSWORDS
cp a-infra-postgres/.env.example a-infra-postgres/.env

# one-shot TLS bootstrap for the etcd cluster (full path, removes itself)
docker compose -f a-infra-postgres/docker-compose.yaml run --rm etcd-certgen

# bring the component up on its own and verify it before stacking more
docker compose -f a-infra-postgres/docker-compose.yaml up -d --build
docker compose -f a-infra-postgres/docker-compose.yaml exec pg-1 \
  patronictl -c /etc/patroni/patroni.yml list
```

Expect one `Leader` + two streaming `Replica`s. **Then flip the etcd
cluster state from `new` to `existing`** — `new` is only valid for the
very first bootstrap from empty volumes:

```bash
# macOS (BSD sed); on Linux drop the ''
sed -i '' 's/^ETCD_INITIAL_CLUSTER_STATE=new/ETCD_INITIAL_CLUSTER_STATE=existing/' \
  a-infra-postgres/etcd.env

# re-apply: recreates only the etcd containers, data volumes persist
docker compose -f a-infra-postgres/docker-compose.yaml up -d
```

(Why this matters and how to verify: [component README](a-infra-postgres/README.md).)

> **Note on volumes:** every volume in this stack has a fixed `nus-*` name,
> so the standalone `-f` commands above and the root `docker compose up`
> share the same data — including the certs volume written by the one-shot.

## 2. The entry tier — lb-a / lb-b (root compose)

The root file owns the stack-wide HAProxy pair and `include:`s every
component that has landed, so this single command is also the "everything
up" command from now on:

```bash
docker compose up -d
```

Verify the routing:

```bash
psql -h localhost -p 5432 -U postgres   # writes -> current PG leader
psql -h localhost -p 5433 -U postgres   # reads  -> replica pool
open http://localhost:8404/stats        # HAProxy live routing view
```

DBA access always goes through the proxies — `lb-a` on the canonical
host ports (5432/5433), `lb-b` on the alternate ones (15432/15433); see
[a-infra-postgres/README.md](a-infra-postgres/README.md#connecting-as-a-dba).

## 3. Next pieces (as they land)

Each future component follows the same pattern, one piece at a time:

```bash
cp <component>/.env.example <component>/.env             # if it has one
docker compose -f <component>/docker-compose.yaml run --rm <one-shot>   # if it has one
docker compose -f <component>/docker-compose.yaml up -d --build         # verify standalone
# then uncomment its include: entry in ./docker-compose.yaml and:
docker compose up -d
```

Build order and per-component details: main README, section 2.8.1.

## Teardown

```bash
docker compose down          # whole stack, keep data volumes
docker compose down -v       # whole stack, destroy data volumes
docker network rm nus-backbone   # only if removing the stack for good
```

After a `-v` teardown, repeat the one-shot steps on the next bring-up
(certs are gone) and set `ETCD_INITIAL_CLUSTER_STATE` back to `new`.
