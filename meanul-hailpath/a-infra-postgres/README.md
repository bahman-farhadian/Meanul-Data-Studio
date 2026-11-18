# a-infra-postgres — one PostgreSQL

Operational state and the NYC LION graph. One process: `hailpath-postgres`.
Clients use `hailpath-postgres:5432`. Debezium tails this database.

Image, memory, and `shared_buffers` are in the root `.env` (from
`.env.example`). `h-bootstrap` creates PostGIS and pgRouting.
`wal_level=logical`. UTC.

`pg_hba.conf`: local sockets, scram-sha-256 on the network, replication
for `postgres` (Debezium's slot).

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-postgres`. Included by the root compose. |
| `postgresql.conf` | Mounted as `config_file`. |
| `pg_hba.conf` | Auth. Mounted. |
| `.env.example` | Standalone copy of the settings this piece reads. |

## Verify

From `meanul-hailpath/`:

```bash
make verify-pg
```

`pg_isready` on `hailpath-postgres`.

## Connecting

User `postgres`, database `hailpath`, password `PG_SUPERUSER_PASSWORD`.
On the host, port `PROXY_PG_PORT` (default 5432).

```bash
make psql
```

Standalone:

```bash
docker network create hailpath-net
cp .env.example .env
docker compose up -d
docker compose exec hailpath-postgres psql -U postgres -c "select version();"
```

## Teardown

```bash
docker compose down      # keep the data volume
docker compose down -v   # drop the volume entry; bind-mount files stay until make destroy
```
