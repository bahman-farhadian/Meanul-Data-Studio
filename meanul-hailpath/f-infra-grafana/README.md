# f-infra-grafana — the live view

Grafana is meant to answer **"what is happening right now"** and to chart
the ClickHouse rollups: trips finishing, position events arriving, where
demand is, hourly/daily stats. It is the only front end in the stack.
This piece brings Grafana up, connects it, and provisions the Hailpath
dashboards from files.

Lab host port **3000** is published on this container.

## What this piece does

Brings up the Grafana container, connects it to ClickHouse, and loads the
dashboards under `provisioning/dashboards/json/`. The connection and the
dashboards are provisioned from files every time Grafana starts, so a
rebuilt container comes back identical to the one it replaced. The data
source is **read-only in the browser** — the file is the truth. The
ClickHouse password is read from the environment at start (`$CH_PASSWORD`
in the data source file), so no password is written into any file in this
repository.

Queries go only to the `hailpath` ClickHouse database (datasource uid
`hailpath-clickhouse`). They use the warehouse table names, never Kafka, Redis,
or Postgres.

| Dashboard | uid | What it shows |
| --- | --- | --- |
| Live operations | `hailpath-live-ops` | Open trips, ingest, last driver map, freshness |
| Driver inspector | `hailpath-driver` | One `driver_id`: trail, status, utilization |
| Trip inspector | `hailpath-trip` | One `trip_id`: status walk, rider trail, fares |
| City now | `hailpath-city` | Zone demand, surge, congestion |
| ClickHouse history | `hailpath-history` | Hourly/daily rollups, percentiles, utilization |
| Marketplace | `hailpath-marketplace` | Fulfilment, cancellation, offers per match, surge |

`python3 f-infra-grafana/check-dashboards.py` (or `make grafana-check`)
asserts every panel uses that datasource and only names tables declared
in `e-infra-clickhouse/ddl/`.

## The plugin is mounted in

Grafana can download data source plugins when it starts, but then every
restart depends on the internet being reachable. `make grafana-plugin-prepare`
installs the ClickHouse plugin into `hailpath-grafana-plugins` once, and
the official Grafana image mounts that directory. The container starts
with no outside calls at all.

**When changing `GRAFANA_CLICKHOUSE_PLUGIN_VERSION`**, delete that plugin
directory and run `make grafana-plugin-prepare` again.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `grafana` and `tiles` services. Official Grafana image. |
| `tiles/` | Self-hosted OSM raster (Planetiler MBTiles + tileserver-gl). |
| `plugin-prepare.sh` | Downloads the ClickHouse plugin into the host plugin directory. |
| `provisioning/datasources/clickhouse.yaml` | The ClickHouse connection, pointing at `hailpath-clickhouse`. |
| `provisioning/dashboards/provider.yaml` | File provider; Grafana watches the JSON directory. |
| `provisioning/dashboards/json/*.json` | The five Hailpath dashboards. |
| `check-dashboards.py` | Static check: datasource uid and ClickHouse table names. |
| `.env.example` | Template for the untracked `.env` (image pins, logins). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone — the whole stack runs UTC. |
| `GRAFANA_IMAGE` | `grafana/grafana:12.4.15` | Base image. |
| `GRAFANA_CLICKHOUSE_PLUGIN_VERSION` | `4.23.5` | Plugin version installed by `make grafana-plugin-prepare`. |
| `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` | `admin` / — (required) | The Grafana login. |
| `CH_USER` / `CH_PASSWORD` | `hailpath` / — (required) | How Grafana logs in to ClickHouse; must match `e-infra-clickhouse/.env`. |
| `TILESERVER_IMAGE` | `maptiler/tileserver-gl:v5.7.0` | Serves PNG tiles from `nyc.mbtiles`. |
| `JAVA_JRE_IMAGE` | `eclipse-temurin:21-jre-noble` | Runs Planetiler (Docker Hub, not ghcr). |
| `PLANETILER_JAR_URL` | GitHub `v0.8.4` | The Planetiler jar `make tiles-prepare` fetches. |
| `GEOFABRIK_NY_PBF_URL` | Geofabrik `us/new-york` | OSM extract for the map. |
| `WATER_POLYGONS_URL` / `NATURAL_EARTH_URL` / `LAKE_CENTERLINES_URL` | osmdata / naciscdn / acalcutt | Host-curl extras. Planetiler itself does not download. |

## Standalone quickstart

```bash
docker network create hailpath-net      # once, shared by the whole stack
cp .env.example .env                    # change both passwords
docker compose up -d --build
```

Grafana host port 3000 is published on this container.

## Verify

```bash
# Grafana is up
docker compose exec hailpath-grafana wget -qO- http://localhost:3000/api/health

# the data source was provisioned and can reach ClickHouse
docker compose exec hailpath-grafana wget -qO- \
  --header="Content-Type: application/json" \
  --http-user="${GRAFANA_ADMIN_USER}" --http-password="${GRAFANA_ADMIN_PASSWORD}" \
  http://localhost:3000/api/datasources
```

In the browser, open <http://localhost:3000>, go to
**Connections → Data sources → ClickHouse** and press **Save & test**. It
should report success. Maps use **self-hosted OSM** from `hailpath-tiles`.
No MapTiler key.

Build the MBTiles once with `make tiles-prepare` (also part of `make prepare`).
That target is idempotent: existing PBF/jar/extras/`nyc.mbtiles` are kept.
`make destroy` keeps `hailpath-tiles-data`; `make nuke` is what deletes it.
Planetiler runs with `--download=false` (MapTiler's CDN does not resolve in
the JRE container).

`make up` starts **grafana and tiles together** (piece f). `make verify`
runs `tiles-health`: `hailpath-tiles` healthy, then GET `/styles.json` and a
real PNG from `hailpath-tiles:8080`.

Dashboards are under **Dashboards → Hailpath**. If tiles is down on a live stack
and you must not destroy:

```bash
docker compose up -d tiles
# wait until healthy, then: make tiles-health
```

## Teardown

```bash
# keep Grafana's own database
docker compose down

# remove it as well (also the right move after a plugin version change)
docker compose down -v
```
