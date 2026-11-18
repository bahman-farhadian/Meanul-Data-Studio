# a-infra-postgres — one PostgreSQL

The system of truth for operational state and the NYC road network.
One process: `hailpath-postgres`. Clients use `hailpath-postgres:5432`.
The lab host port is published on this container. Debezium tails this
same database.

The image is the official `pgrouting/pgrouting:18-3.6-3.8.0` (Postgres 18,
PostGIS 3.6, pgRouting 3.8). Pulled, not built. `h-bootstrap` creates the
extensions. `wal_level=logical` is set so Debezium can open a `pgoutput`
slot. Timestamps are UTC.

Memory is `PG_MEM` in the root `.env`, together with `shared_buffers` and
the other `-c` flags in compose. Laptop: `PG_MEM=3g` and
`PG_SHARED_BUFFERS=768MB`. Deploy: `PG_MEM=16g` and `shared_buffers=4GB`.
A 9 GB cap was not enough on the deploy host: the process was killed near
8.3 GB.

Clients use `PG_HOST=hailpath-postgres`. Port **5432** is
`hailpath-postgres`. Debezium uses that port.

`pg_hba.conf` allows local sockets, scram-sha-256 for network logins,
and a replication line for the `postgres` user. A replication connection
does not match `all`, and the logical slot needs that line.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-postgres`. Included by the root compose. |
| `pg_hba.conf` | Local trust, network scram, replication for `postgres`. Mounted into the container. |
| `.env.example` | Image pin, database name, superuser password. |

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone. |
| `PG_IMAGE` | `pgrouting/pgrouting:18-3.6-3.8.0` | Postgres 18 with PostGIS 3.6 and pgRouting 3.8. Pulled, not built. |
| `PG_DATABASE` | `hailpath` | Database created on first start. |
| `PG_SUPERUSER_PASSWORD` | required | `postgres` password. |
| `PG_CPUS` | `10` | CPU ceiling after bootstrap. |
| `PG_MEM` | `3g` laptop / `16g` deploy | Memory ceiling. Move with `shared_buffers` in compose. |

The root `.env` is the file `make up` reads. This directory's
`.env.example` is the standalone copy.

## Verify

From `meanul-hailpath/`:

```bash
make verify-pg
```

That runs `pg_isready` on `hailpath-postgres`.

## Connecting

One listen. Host port `PROXY_PG_PORT` (default 5432).

| Port | What it is |
| --- | --- |
| `5432` | The one PostgreSQL. Debezium uses this port. |

User `postgres`, database `hailpath`, password `PG_SUPERUSER_PASSWORD`.

```bash
make psql
```

Standalone:

```bash
docker network create hailpath-net
cp .env.example .env
docker compose up -d --build
docker compose exec hailpath-postgres psql -U postgres -c "select version();"
```

## Teardown

```bash
docker compose down      # keep the data volume
docker compose down -v   # drop the volume entry; the bind mount's files stay until make destroy
```
