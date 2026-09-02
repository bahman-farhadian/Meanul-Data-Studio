# b-infra-redis — nus-cache: Sentinel-managed Redis

The stack's **read path**. Everything except the writers reads from here:
generators, dispatch, the city service, and the ClickHouse sink look up
profiles, active trips, hotspot scores, and the driver geo index in Redis
and never query the PostgreSQL OLTP cluster for them (root README,
section 1). `cache-updater` keeps it in step with PostgreSQL by applying
Debezium's CDC stream.

Three data nodes (`redis-1/2/3`) and three Sentinels (`sentinel-1/2/3`).
**The primary is elected, not assigned** — exactly like the Patroni leader
in `a-infra-postgres` — so all three data nodes carry identical limits and
identical config. `redis-1` is only the seed primary of a cold start; after
the first failover the cluster's own state decides.

> **Naming:** the `nus-` prefix on shared resources (`nus-cache`, `nus-pg`,
> `nus-etcd`, `nus-backbone`) is the acronym of **n**ot-**u**ber-**s**ervice.

Nothing publishes ports to the host. Unlike PostgreSQL, Redis is **not**
reached through the `lb-a`/`lb-b` HAProxy pair: clients ask Sentinel for the
current primary and reconnect themselves. A TCP proxy in the middle would
happily hand a client a connection to a node Sentinel had already demoted,
which is precisely the failure Sentinel exists to prevent.

## Config-file lifecycle — the one thing to know

Sentinel **rewrites config files at runtime**: on every promotion or
demotion it issues `REPLICAOF` + `CONFIG REWRITE` to the data nodes, and it
continuously rewrites its own file with the known replicas, known sentinels,
current epoch, and the current primary. So the live config cannot be a
read-only bind mount, and it must survive a restart.

The pattern used here, on both roles:

1. the committed file (`redis/redis.conf`, `sentinel/sentinel.conf`) is a
   **template**, mounted read-only at `/templates/`;
2. `entrypoint.sh` copies it to `/data/…conf` **only if that file does not
   exist**, appending the per-node values and secrets (identity, auth,
   maxmemory, seed `replicaof` / `monitor` block);
3. from then on the file on the volume is the live config and the entrypoint
   leaves it alone.

Secrets and per-node values are **appended**, not substituted — no `sed`, so
no delimiter or escaping hazard in a password.

**Consequence:** after a node's first start, editing the template or
`REDIS_PASSWORD` changes nothing for that node. To roll a password, either
edit `/data/redis.conf` in place and `CONFIG SET requirepass`, or drop the
volumes and start clean (see [Teardown](#teardown)).

## Security posture

- **Data nodes** require a password (`requirepass`), use the same secret for
  replication (`masterauth`), and Sentinel authenticates with it
  (`sentinel auth-pass`).
- **Sentinel itself is unauthenticated** and runs with `protected-mode no`.
  It publishes no host port and is reachable only inside `nus-backbone`.
  This is the same deliberate trade as Patroni's open monitoring endpoints
  in piece a: discovery must never be the thing that breaks. Anything that
  can reach the Docker network can read the topology — it cannot read the
  data.

## Memory policy

`maxmemory` (2560 MB by default) sits well below the 4 GB container limit
because an AOF-rewrite fork copies dirty pages and `memswap_limit ==
mem_limit` leaves no swap to absorb the spike.

The eviction policy is **`noeviction`**, not an LRU. Redis holds live
operational state written directly by the services —
`geo:drivers:available`, `trip:{id}:active` — not just CDC-replayable copies
of PostgreSQL rows. Evicting those silently would corrupt dispatch
decisions; refusing the write is loud, and shows up immediately in the
service logs.

Persistence is **AOF only** (`save ""`), so a busy node never forks twice
for two persistence mechanisms; replication uses diskless sync for the same
reason.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `redis-1/2/3` + `sentinel-1/2/3`; included by the root compose. |
| `redis/redis.conf` | Data-node template: network, memory, persistence, replication tuning. |
| `redis/entrypoint.sh` | Materialises `/data/redis.conf` once, appends identity/auth/maxmemory/seed `replicaof`, execs `redis-server`. |
| `sentinel/sentinel.conf` | Sentinel template: hostname resolution/announcement, logging. |
| `sentinel/entrypoint.sh` | Materialises `/data/sentinel.conf` once, appends the `monitor` block in the required order, execs `redis-sentinel`. |
| `.env.example` | Template for the untracked `.env` (image pin, TZ, password, failover tuning). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone — the whole stack runs UTC. |
| `REDIS_IMAGE` | `redis:8.10.1` | Pinned image; serves both roles (`redis-sentinel` ships in it). |
| `REDIS_MASTER_NAME` | `nus-cache` | The name clients ask Sentinel about. |
| `REDIS_MASTER_HOST` | `redis-1` | Seed primary, cold start only — ignored once the cluster has state. |
| `REDIS_PASSWORD` | — (required) | `requirepass` + `masterauth` + `sentinel auth-pass`. |
| `REDIS_MAXMEMORY` | `2560mb` | Dataset ceiling per node, below the container limit. |
| `SENTINEL_QUORUM` | `2` | Sentinels that must agree the primary is down (of 3). |
| `SENTINEL_DOWN_AFTER_MS` | `5000` | How long a node must look down before it counts as down. |
| `SENTINEL_FAILOVER_TIMEOUT_MS` | `15000` | Failover attempt timeout. |

## Standalone quickstart

> Standalone runs use the `b-infra-redis` project scope. The `nus-*` volumes
> are shared with the full stack, but Docker labels each volume with the
> project that created it — mixing scopes triggers a harmless
> `volume ... was created for project ...` warning on `up`. For stack
> assembly, run everything through the root compose per the
> [runbook](../README.md).

```bash
# one-time: the shared network every stack component joins
docker network create nus-backbone

# copy the settings template, then change REDIS_PASSWORD
cp .env.example .env

docker compose up -d
```

## Verify

`redis-cli` inside the data-node containers picks the password up from
`REDISCLI_AUTH`, so no `-a` flag is needed.

```bash
# roles: exactly one "master", two "slave" (Redis's wire-level wording)
docker compose exec redis-1 redis-cli role
docker compose exec redis-2 redis-cli role
docker compose exec redis-3 redis-cli role

# what Sentinel believes: current primary address, its replicas, its peers
docker compose exec sentinel-1 redis-cli -p 26379 sentinel get-master-addr-by-name nus-cache
docker compose exec sentinel-1 redis-cli -p 26379 sentinel replicas nus-cache
docker compose exec sentinel-1 redis-cli -p 26379 sentinel sentinels nus-cache

# can this Sentinel set actually run a failover right now?
docker compose exec sentinel-1 redis-cli -p 26379 sentinel ckquorum nus-cache

# write on the primary, read it back on a replica (replication works)
docker compose exec redis-1 redis-cli set smoke:key hello
docker compose exec redis-2 redis-cli get smoke:key
docker compose exec redis-1 redis-cli del smoke:key
```

Expected shape of a healthy set: three sentinels that all agree on one
primary, two replicas listed with `flags=slave` and no `s_down`/`o_down`,
and `ckquorum` answering `OK`.

## Failover demo

```bash
# planned: ask Sentinel to promote a replica (assume redis-1 leads)
docker compose exec sentinel-1 redis-cli -p 26379 sentinel failover nus-cache

# or unplanned: kill the current primary
docker stop redis-1

# after SENTINEL_DOWN_AFTER_MS + election, a replica has been promoted
docker compose exec sentinel-2 redis-cli -p 26379 sentinel get-master-addr-by-name nus-cache

# the stopped node rejoins as a replica of the new primary — Sentinel
# reconfigures it on the way in
docker start redis-1
docker compose exec redis-1 redis-cli role
```

Clients are expected to reconnect through Sentinel; anything that cached a
primary address and never re-asked will fail here, which is the point of
running the drill.

## Teardown

```bash
# keep data volumes (and with them the live topology)
docker compose down

# destroy data + sentinel state volumes too — this is also how you reset
# the materialised configs after changing REDIS_PASSWORD or the templates
docker compose down -v
```
