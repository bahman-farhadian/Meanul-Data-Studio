# b-infra-redis — one Redis

The read path. Generators, dispatch, the city service, and the
ClickHouse sink look up profiles, active trips, hotspot scores, and the
driver geo index here. They do not query PostgreSQL for those lookups.
`cache-updater` applies Debezium's CDC stream.

One process, `nus-redis` / `nus-redis`. Clients use `REDIS_HOST`
(default `nus-redis`, `nus-redis` in the root example).

HAProxy publishes that process on **6379** and **6380**. Both ports are
`nus-redis`. `make verify-redis`
is a PING.

Logical databases are fixed in
`z-lib/nus-common/nus_common/redis_client.py`:

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

`REDIS_PASSWORD` must not contain whitespace. The HAProxy check sends
`AUTH` as one inline command, and Redis splits that on spaces.

`maxmemory` defaults to 2560 MB, under the 4 GB container limit, because
an AOF rewrite forks and there is no swap. The policy is `noeviction`.
Live keys such as `geo:drivers:available` and `trip:{id}:active` are not
safe to evict. Persistence is AOF only (`save ""`).

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `nus-redis`. Included by the root compose. |
| `redis/redis.conf` | Template: network, memory, persistence. |
| `redis/entrypoint.sh` | Copies the template once and appends the password and `maxmemory`. |
| `.env.example` | Image pin, password, `maxmemory`. |

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone. |
| `REDIS_IMAGE` | `redis:8.10.1` | Pinned image. |
| `REDIS_PASSWORD` | required | `requirepass`. |
| `REDIS_MAXMEMORY` | `2560mb` | Dataset ceiling, below the container limit. |
| `REDIS_HOST` | `nus-redis` | Address clients open. The root example sets `nus-redis`. |

## Verify

```bash
make verify-redis
```

A `PONG` is the check. From this directory alone:

```bash
docker network create nus-backbone
cp .env.example .env
docker compose up -d
docker compose exec nus-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping
```

## Teardown

```bash
docker compose down
docker compose down -v
```
