# e-infra-clickhouse — one ClickHouse

One process: `hailpath-clickhouse`. `clickhouse-sink` writes events here,
`h-bootstrap` loads history here, Grafana reads here.

Image, memory, and query ceilings are in the root `.env` (from
`.env.example`). Laptop: `CH_MEM=2g`. Deploy: `CH_MEM=8g`. UTC.

Engines are MergeTree (raw), SummingMergeTree / AggregatingMergeTree /
ReplacingMergeTree (rollups). Query names are the tables. There is no
`*_local` suffix. TTL on each table bounds disk. Full list: `ddl/`.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-clickhouse` and `ch-ddl-init`. |
| `config/clickhouse/config.d/memory.xml` | Server memory ceiling. Mounted. |
| `config/clickhouse/users.d/profiles.xml` | Per-query limits. Mounted. |
| `config/clickhouse/users.d/grants.xml` | Grants on `hailpath.*` only. |
| `ddl/*.sql` | Tables, in name order. `IF NOT EXISTS`. |
| `ddl/apply-ddl.sh` | Applies those files. Safe to re-run. |
| `.env.example` | Standalone copy of the settings this piece reads. |

## Verify

From `meanul-hailpath/`:

```bash
make verify-ch
```

Lists MergeTree tables in database `hailpath`.

## Connecting

User `CH_USER` (default `hailpath`), database `hailpath` (`CLICKHOUSE_DB`),
password `CH_PASSWORD`. `make ch-client` and Grafana open database
`hailpath`. Grants are on `hailpath.*` only (`users.d/grants.xml`). The
image still keeps an empty `default` database; do not set that as the
user's default in `grants.xml` or first boot cannot create `hailpath`.
On the network:
`hailpath-clickhouse:8123` (HTTP) and `:9000` (native). On the host:
`PROXY_CH_HTTP_PORT` and `PROXY_CH_NATIVE_PORT`.

```bash
make ch-client
```

Standalone:

```bash
docker network create hailpath-net
cp .env.example .env
docker compose up -d
docker compose run --rm ch-ddl-init
```

## Adding a table

Add a numbered file under `ddl/` and run `make ch-ddl`.

## Teardown

```bash
docker compose down      # keep the data volume
docker compose down -v   # drop the volume entry; bind-mount files stay until make destroy
```

After a volume wipe, run `make ch-ddl` again on the next start.
