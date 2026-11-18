# b-infra-redis — one Redis

The read path. Generators, dispatch, city, and the ClickHouse sink look
up profiles, active trips, hotspots, and the driver geo index here. They
do not query PostgreSQL for those lookups. `cache-updater` applies
Debezium's CDC stream.

One process: `hailpath-redis`. Clients use `REDIS_HOST` (default
`hailpath-redis`). `make verify-redis` is a PING.

Host publish is `PROXY_REDIS_PORT` (default 6379). Image, `REDIS_MEM`, and
`REDIS_MAXMEMORY` are in the root `.env`.

Logical databases (`z-lib/hailpath-common/hailpath_common/redis_client.py`):

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
already has that file.

`REDIS_PASSWORD` must not contain whitespace. Policy is `noeviction`.
Persistence is AOF only (`save ""`).

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-redis`. |
| `redis/redis.conf` | Template: network, memory, persistence. |
| `redis/entrypoint.sh` | First-start copy; appends password and `maxmemory`. |
| `.env.example` | Standalone copy of the settings this piece reads. |

## Verify

```bash
make verify-redis
```

## Teardown

```bash
docker compose down
docker compose down -v
```
