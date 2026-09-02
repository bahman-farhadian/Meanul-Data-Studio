# f-infra-grafana — the live view

Grafana answers **"what is happening right now"**: trips finishing, position
events arriving, where demand is, and how often the route prediction was
wrong. Superset (piece `g`) is the slower analytical view. Both read the
same ClickHouse cluster.

Reached through the entry tier on **port 3000** (`lb-a`) or **13000**
(`lb-b`). Grafana itself publishes no host port.

## Everything is provisioned from files

The ClickHouse connection and the dashboards are created from the files in
this directory every time Grafana starts. Nothing has to be clicked
together after a rebuild, and a fresh container is identical to the one it
replaced.

Two consequences worth knowing:

- the data source is **read-only in the browser** — the file is the truth;
- a dashboard edited in the browser is **not** written back to
  `dashboards/`. To keep a change: export the dashboard as JSON from
  Grafana's share menu, save it over the file here, and commit it. The
  provider reloads files every 30 seconds, so the change appears without a
  restart.

The ClickHouse password is read from the environment at start
(`$CH_PASSWORD` in the data source file), so no password is written into any
file in this repository.

## The plugin is baked into the image

Grafana can download data source plugins when it starts, but then every
restart depends on the internet being reachable. The `Dockerfile` installs
the ClickHouse plugin at build time instead, so the container starts with no
outside calls at all.

**When changing `GRAFANA_CLICKHOUSE_PLUGIN_VERSION`**, remove the
`nus-grafana-data` volume as well. Grafana keeps plugins under
`/var/lib/grafana`, which is that volume, and an existing volume hides the
newer copy in the rebuilt image.

## The shipped dashboard

`dashboards/nus-live.json` — four numbers across the top (completed trips,
revenue, share of trips slower than predicted, average surge), position
events per minute, and the busiest zones right now.

All panels read the **Distributed** tables (`nus.trip_stats_hourly`,
`nus.driver_positions`, ...), so they see both shards. The summary panels
aggregate with `sum()` because the hourly summary is filled per node — see
[`../e-infra-clickhouse/README.md`](../e-infra-clickhouse/README.md#reading-the-hourly-summary-correctly).

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `grafana` service. |
| `Dockerfile` | Grafana with the ClickHouse plugin baked in. |
| `provisioning/datasources/clickhouse.yaml` | The ClickHouse connection, pointing at `lb-a`. |
| `provisioning/dashboards/dashboards.yaml` | Tells Grafana to load every dashboard file. |
| `dashboards/nus-live.json` | The live dashboard. |
| `.env.example` | Template for the untracked `.env` (image pins, logins). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone — the whole stack runs UTC. |
| `GRAFANA_IMAGE` | `grafana/grafana:12.4.10` | Base image. |
| `GRAFANA_CLICKHOUSE_PLUGIN_VERSION` | `4.21.2` | Plugin version baked into the image. |
| `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` | `admin` / — (required) | The Grafana login. |
| `CH_USER` / `CH_PASSWORD` | `nus` / — (required) | How Grafana logs in to ClickHouse; must match `e-infra-clickhouse/.env`. |

## Standalone quickstart

```bash
docker network create nus-backbone      # once, shared by the whole stack
cp .env.example .env                    # change both passwords
docker compose up -d --build
```

Standalone, Grafana has no proxy in front of it, so reach it by exec'ing
into the container or by running the full stack, where `lb-a` publishes it
on port 3000.

## Verify

```bash
# Grafana is up
docker compose exec grafana wget -qO- http://localhost:3000/api/health

# the data source was provisioned and can reach ClickHouse
docker compose exec grafana wget -qO- \
  --header="Content-Type: application/json" \
  --http-user="${GRAFANA_ADMIN_USER}" --http-password="${GRAFANA_ADMIN_PASSWORD}" \
  http://localhost:3000/api/datasources

# the dashboard was loaded
docker compose exec grafana wget -qO- \
  --http-user="${GRAFANA_ADMIN_USER}" --http-password="${GRAFANA_ADMIN_PASSWORD}" \
  "http://localhost:3000/api/search?query=not-uber-service"
```

In the browser, open <http://localhost:3000>, go to
**Connections → Data sources → ClickHouse** and press **Save & test**. It
should report success. Until the app services run, the panels will be empty
but must not show errors — an empty panel means "no data yet", an error
means the connection or a query is wrong.

## When a panel shows an error

Panel queries are plain SQL against the Distributed tables, so the fastest
way to tell a Grafana problem from a data problem is to run the same query
directly:

```bash
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT sum(completed_trips) FROM nus.trip_stats_hourly WHERE hour >= now() - INTERVAL 1 HOUR"
```

If that works and the panel does not, the problem is in the panel; fix it in
the browser, export the dashboard, and commit the file.

## Teardown

```bash
# keep Grafana's own database
docker compose down

# remove it as well (also the right move after a plugin version change)
docker compose down -v
```
