# a-infra-postgres — one PostgreSQL

The system of truth for operational state and the NYC road network.
One process, `pg-1` / `nus-pg-1`. HAProxy publishes it on stable host
ports. Debezium tails this same database.

The image is `postgres:18.6` with PostGIS and pgRouting installed.
`h-bootstrap` creates the extensions. `wal_level=logical` is set so
Debezium can open a `pgoutput` slot. Timestamps are UTC.

`shared_buffers` is 4 GB. `.env.example` sets `PG_MEM=16g` for that
process. A 9 GB cap was not enough: the process was killed near 8.3 GB.

Clients use `PG_HOST=nus-lb-a`. Port **5432** and port **5433** are both
`nus-pg-1`. Debezium uses 5432.

`pg_hba.conf` allows local sockets, scram-sha-256 for network logins,
and a replication line for the `postgres` user. A replication connection
does not match `all`, and the logical slot needs that line.

`etcd.env` is still in this directory. The process does not read it.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `pg-1`. Included by the root compose. |
| `Dockerfile` | `postgres:18.6` plus PostGIS and pgRouting. |
| `pg_hba.conf` | Local trust, network scram, replication for `postgres`. |
| `etcd.env` | Unused by this process. Left in the tree. |
| `.env.example` | Image pin, database name, superuser password. |

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone. |
| `PG_IMAGE` | `postgres:18.6` | Base image for the build. |
| `PG_DATABASE` | `nus` | Database created on first start. |
| `PG_SUPERUSER_PASSWORD` | required | `postgres` password. |
| `PG_CPUS` | `10` | CPU ceiling after bootstrap. |
| `PG_MEM` | `16g` in the root example | Memory ceiling. Do not set this below 16 GB. |

The root `.env` is the file `make up` reads. This directory's
`.env.example` is the standalone copy.

## Verify

From `not-uber-service/`:

```bash
make verify-pg
```

That runs `pg_isready` on `nus-pg-1`.

## Connecting

In the full stack, connect through HAProxy. Both ports are the one
process.

| Port | What it is |
| --- | --- |
| `5432` | Write port. Debezium uses this one. |
| `5433` | The same process. |

User `postgres`, database `nus`, password `PG_SUPERUSER_PASSWORD`.

```bash
make psql
make psql-read
```

`make psql` uses port 5432. `make psql-read` uses port 5433. Both land
on `nus-pg-1`.

HAProxy stats: <http://localhost:8404/stats>. A down server on `pg_write`
or `pg_read` means `nus-pg-1` failed that check.

For this directory alone, with no proxy:

```bash
docker network create nus-backbone
cp .env.example .env
docker compose up -d --build
docker compose exec pg-1 psql -U postgres -c "select version();"
```

## Teardown

```bash
docker compose down      # keep the data volume
docker compose down -v   # drop the volume entry; the bind mount's files stay until make destroy
```
