# f-infra-grafana — the live view

Grafana is meant to answer **"what is happening right now"** and to chart
the ClickHouse rollups: trips finishing, position events arriving, where
demand is, hourly/daily stats. Superset (piece `g`) is a separate SQL Lab
tool; it is not used by these dashboards. Both *can* read the same
ClickHouse cluster. This piece brings Grafana up, connects it, and
provisions the NUS dashboards from files.

Reached through the entry tier on **port 3000** (`lb-a`) or **13000**
(`lb-b`). Grafana itself publishes no host port.

## What this piece does

Brings up the Grafana container, connects it to ClickHouse, and loads the
dashboards under `provisioning/dashboards/json/`. The connection and the
dashboards are provisioned from files every time Grafana starts, so a
rebuilt container comes back identical to the one it replaced. The data
source is **read-only in the browser** — the file is the truth. The
ClickHouse password is read from the environment at start (`$CH_PASSWORD`
in the data source file), so no password is written into any file in this
repository.

Queries go only to the `nus` ClickHouse database (datasource uid
`nus-clickhouse`). They use Distributed table names, never Kafka, Redis,
or Postgres.

| Dashboard | uid | What it shows |
| --- | --- | --- |
| Live operations | `nus-live-ops` | Open trips, ingest, last driver map, freshness |
| Driver inspector | `nus-driver` | One `driver_id`: trail, status, utilization |
| Trip inspector | `nus-trip` | One `trip_id`: status walk, rider trail, fares |
| City now | `nus-city` | Zone demand, surge, congestion |
| ClickHouse history | `nus-history` | Hourly/daily rollups, percentiles, utilization |

`python3 f-infra-grafana/check-dashboards.py` (or `make grafana-check`)
asserts every panel uses that datasource and only names tables declared
in `e-infra-clickhouse/ddl/`.

## The plugin is baked into the image

Grafana can download data source plugins when it starts, but then every
restart depends on the internet being reachable. The `Dockerfile` installs
the ClickHouse plugin at build time instead, so the container starts with no
outside calls at all.

**When changing `GRAFANA_CLICKHOUSE_PLUGIN_VERSION`**, remove the
`nus-grafana-data` volume as well. Grafana keeps plugins under
`/var/lib/grafana`, which is that volume, and an existing volume hides the
newer copy in the rebuilt image.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `grafana` service. |
| `Dockerfile` | Grafana with the ClickHouse plugin baked in. |
| `provisioning/datasources/clickhouse.yaml` | The ClickHouse connection, pointing at `nus-lb-a`. |
| `provisioning/dashboards/provider.yaml` | File provider; Grafana watches the JSON directory. |
| `provisioning/dashboards/json/*.json` | The five NUS dashboards. |
| `check-dashboards.py` | Static check: datasource uid and ClickHouse table names. |
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
```

In the browser, open <http://localhost:3000>, go to
**Connections → Data sources → ClickHouse** and press **Save & test**. It
should report success. Dashboards are under **Dashboards → NUS**. On a
running full stack, recreate Grafana only:

```bash
docker compose up -d grafana
```

## Teardown

```bash
# keep Grafana's own database
docker compose down

# remove it as well (also the right move after a plugin version change)
docker compose down -v
```
