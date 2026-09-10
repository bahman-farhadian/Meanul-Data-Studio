# f-infra-grafana — the live view

Grafana answers **"what is happening right now"**: trips finishing, position
events arriving, where demand is, and how often the route prediction was
wrong. Superset (piece `g`) is the slower analytical view. Both read the
same ClickHouse cluster.

Reached through the entry tier on **port 3000** (`lb-a`) or **13000**
(`lb-b`). Grafana itself publishes no host port.

## Everything is provisioned from files

The ClickHouse connection is created from the file in this directory every
time Grafana starts. Nothing has to be clicked together after a rebuild,
and a fresh container is identical to the one it replaced.

The data source is **read-only in the browser** — the file is the truth. The
ClickHouse password is read from the environment at start (`$CH_PASSWORD`
in the data source file), so no password is written into any file in this
repository.

**Dashboards are deliberately not provisioned at this stage.** `dashboards/`
is empty on purpose — this piece currently ships the container and the
ClickHouse connection only. Panel content depends on tables, materialized
views and data-generation logic (pieces `h` onward) that have not been
verified correct yet, so building dashboards against them now would mean
redoing that work later. A real dashboard *was* designed and built earlier
(two dashboards, described below for when that work resumes) and is still in
git history — `git log --diff-filter=D -- f-infra-grafana/dashboards/` — not
lost, just not currently loaded. `provisioning/dashboards/dashboards.yaml`
is still here and harmless: it loads whatever `dashboards/` contains, which
right now is nothing.

If a dashboard is added back later: export it as JSON from Grafana's share
menu (or restore the old file from git history), save it into `dashboards/`,
and commit it. The provider reloads files every 30 seconds, so it appears
without a restart. A dashboard edited only in the browser is never written
back to this directory — that export step is what makes it durable.

## The plugin is baked into the image

Grafana can download data source plugins when it starts, but then every
restart depends on the internet being reachable. The `Dockerfile` installs
the ClickHouse plugin at build time instead, so the container starts with no
outside calls at all.

**When changing `GRAFANA_CLICKHOUSE_PLUGIN_VERSION`**, remove the
`nus-grafana-data` volume as well. Grafana keeps plugins under
`/var/lib/grafana`, which is that volume, and an existing volume hides the
newer copy in the rebuilt image.

## The two dashboards (designed, not currently provisioned)

Not active right now — see the note above. Kept here as the design record
for when this work resumes.

`nus-live.json` was four numbers across the top (completed trips, revenue,
share of trips slower than predicted, average surge), position events per
minute, and the busiest zones right now.

All panels read the **Distributed** tables (`nus.trip_stats_hourly`,
`nus.driver_positions`, ...), so they see both shards. The summary panels
aggregate with `sum()` because the hourly summary is filled per node — see
[`../e-infra-clickhouse/README.md`](../e-infra-clickhouse/README.md#reading-the-hourly-summary-correctly).
That constraint still applies to any panel written against these tables in
the future.

| Dashboard | Answers |
| --- | --- |
| **not-uber-service - live** | Is the platform working right now? Six headline figures, position events arriving, the busiest zones, and how every trip in the window ended. |
| **not-uber-service - fleet** | Where are the drivers? A map of every driver at their last known position, coloured by what they are doing, beside supply and demand per zone. |

Two panels are worth knowing about because they are diagnostics rather than
business figures:

- **Warehouse is behind by** — the age of the newest trip event. If it climbs
  and does not come back, something between Kafka and the sink has stalled.
  Check `make lag` before anything else.
- **Nobody available** — the share of trips that found no driver. This is the
  number that says the fleet is too small for the hour; the fix is fewer
  requests or more drivers, never more containers.

Colours are assigned by the job they do, not by taste. The four trip endings
take the reserved status colours, so completed is always the same green and
"nobody available" always the same red. The three driver states on the map are
the maximum that stay distinguishable for colour-vision deficiency when every
pair can appear side by side, which happens to be exactly how many states an
online driver has.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `grafana` service. |
| `Dockerfile` | Grafana with the ClickHouse plugin baked in. |
| `provisioning/datasources/clickhouse.yaml` | The ClickHouse connection, pointing at `nus-lb-a`. |
| `provisioning/dashboards/dashboards.yaml` | Tells Grafana to load every dashboard file — currently loads nothing, see above. |
| `dashboards/` | Empty for now on purpose. The removed dashboards are in git history. |
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
should report success. There is no dashboard to check yet — see the note
above.

Once a dashboard is provisioned again, the fastest way to tell a Grafana
problem from a data problem is to run a panel's query directly against
ClickHouse and compare:

```bash
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT sum(completed_trips) FROM nus.trip_stats_hourly WHERE hour >= now() - INTERVAL 1 HOUR"
```

If that works and the panel does not, the problem is in the panel, not the
connection.

## Teardown

```bash
# keep Grafana's own database
docker compose down

# remove it as well (also the right move after a plugin version change)
docker compose down -v
```
