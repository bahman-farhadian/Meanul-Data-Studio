# b-infra-redis — one Redis

The read path. Generators, dispatch, the city service, and the
ClickHouse sink look up profiles, active trips, hotspot scores, and the
driver geo index here. They do not query PostgreSQL for those lookups.
`cache-updater` applies Debezium's CDC stream.

One process: `hailpath-redis`. Clients on hailpath-net use `REDIS_HOST`
(default `hailpath-redis`). `make verify-redis` is a PING.

Lab host port is `PROXY_REDIS_PORT` (default 6379). On this workstation
it is 16379 because host 6379 is already taken. Production would not
publish Redis.

Logical databases are fixed in
`z-lib/hailpath-common/hailpath_common/redis_client.py`:

| DB | Constant | Keys |
| --- | --- | --- |
| 0 | `DB_SYSTEM` | `system:bootstrap:done` |
| 1 | `DB_DRIVER` | `driver:*`, `vehicle:*`, geo available sets |
| 2 | `DB_PASSENGER` | `passenger:*` |
| 3 | `DB_TRIP` | `trip:*`, `trip:*:active` |
| 4 | `DB_DEMAND` | `hotspot:*`, `zone:*` |

## Config

`redis/redis.conf` is a template. `entrypoint.sh` copies it to
`/data/redis.conf` on first start and appends `requirepass` and
`maxmemory`. Later edits to the template do not change a volume that
already has that file. To roll a password, edit the live file and
`CONFIG SET`, or drop the volume and start clean.

`REDIS_PASSWORD` must not contain whitespace. Redis AUTH splits on spaces.

Memory is `REDIS_MEM` in the root `.env`, with `REDIS_MAXMEMORY` below it
because an AOF rewrite forks and there is no swap. Laptop: `REDIS_MEM=640m`
and `REDIS_MAXMEMORY=384mb`. Deploy: `4g` and `2560mb`. The policy is
`noeviction`. Live keys such as `geo:drivers:available` and
`trip:{id}:active` are not safe to evict. Persistence is AOF only
(`save ""`).

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-redis`. Included by the root compose. |
| `redis/redis.conf` | Template: network, memory, persistence. |
| `redis/entrypoint.sh` | Copies the template once and appends the password and `maxmemory`. |
| `.env.example` | Image pin, password, `maxmemory`. |

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone. |
| `REDIS_IMAGE` | `redis:8.10.3` | Pinned image. |
| `REDIS_PASSWORD` | required | `requirepass`. |
| `REDIS_MAXMEMORY` | `384mb` laptop / `2560mb` deploy | Dataset ceiling, below `REDIS_MEM`. |
| `REDIS_HOST` | `hailpath-redis` | Address clients open. The root example sets `hailpath-redis`. |

## Verify

```bash
make verify-redis
```

A `PONG` is the check. From this directory alone:

```bash
docker network create hailpath-net
cp .env.example .env
docker compose up -d
docker compose exec hailpath-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping
```

## Teardown

```bash
docker compose down
docker compose down -v
```
