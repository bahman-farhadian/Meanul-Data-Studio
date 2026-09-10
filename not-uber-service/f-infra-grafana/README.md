# f-infra-grafana — the live view

Grafana is meant to answer **"what is happening right now"**: trips
finishing, position events arriving, where demand is. Superset (piece `g`)
is the slower analytical view. Both read the same ClickHouse cluster. At
this stage this piece only brings the tool up and connects it — see below.

Reached through the entry tier on **port 3000** (`lb-a`) or **13000**
(`lb-b`). Grafana itself publishes no host port.

## What this piece does

Brings up the Grafana container and connects it to ClickHouse. Nothing else.
The connection is provisioned from a file in this directory every time
Grafana starts, so a rebuilt container comes back identical to the one it
replaced. The data source is **read-only in the browser** — the file is the
truth. The ClickHouse password is read from the environment at start
(`$CH_PASSWORD` in the data source file), so no password is written into
any file in this repository.

**Dashboard provisioning is not part of this piece.** A dashboard was
designed and built earlier (two of them, live + fleet) and is still findable
in git history — `git log --diff-filter=D -- f-infra-grafana/` — but is not
coming back into this directory. Dashboard content depends on tables,
materialized views and data-generation logic (pieces `h` onward) not yet
verified correct, so it becomes its own separate piece once that is done,
not something bolted back onto infrastructure.

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
should report success. There is nothing else to check yet — no dashboard is
provisioned by this piece.

## Teardown

```bash
# keep Grafana's own database
docker compose down

# remove it as well (also the right move after a plugin version change)
docker compose down -v
```
