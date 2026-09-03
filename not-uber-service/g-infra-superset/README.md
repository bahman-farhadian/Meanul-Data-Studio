# g-infra-superset — the analytical view

Superset is where questions are **explored**: write SQL in SQL Lab, turn the
result into a chart, put charts on a dashboard. Grafana (piece `f`) answers
what is happening right now; Superset answers what has been happening.
Both read the same ClickHouse cluster.

Reached through the entry tier on **port 8088** (`lb-a`) or **18088**
(`lb-b`). Superset itself publishes no host port.

## Superset's own database

Superset stores users, saved charts and dashboards in a small database of
its own. That is a different thing from the data it shows.

This stack uses **SQLite on a named volume** for it. The reason is in the
main README (section 2.9): the stack is documented for a single dashboard
user, and a whole extra PostgreSQL cluster for Superset's bookkeeping would
cost memory the analytics nodes need. The OLTP cluster is not an option
either — dashboards must never touch it.

SQLite has one rule: **one writer at a time**. That is why Superset runs
with exactly one worker, spelled out in `docker-compose.yaml` next to the
reason. If this ever needs to serve a team, the honest fix is to move
Superset's own database to PostgreSQL and raise the worker count — not to
raise the worker count alone.

## The one-shot

`superset-init` does four things, and can be run again at any time:

1. creates or upgrades Superset's own tables;
2. creates the admin user (skipped if it exists);
3. loads the built-in roles and permissions;
4. registers the ClickHouse connection, updating it if it is already there.

Run it after the first `up`, after a Superset version change, and whenever
the ClickHouse password changes.

## About `SUPERSET_SECRET_KEY`

It signs session cookies **and** encrypts the ClickHouse password Superset
saved. Change it and two things happen: everybody is logged out, and
Superset can no longer read back that stored password. Both are fixable —
run `superset-init` again — but it is better to set it once and leave it
alone.

## What ships in the box

Superset comes up populated. `init/register_database.py` registers two
connections and `assets/` holds the datasets, charts and dashboard, imported
by the one-shot — the same "provisioned from files" contract Grafana has.

| Connection | Points at | Why |
| --- | --- | --- |
| `ClickHouse (nus)` | `lb-a:8123` | The warehouse every chart reads. |
| `PostgreSQL (nus, read-only)` | `lb-a:5433` | The OLTP source, for exploration in SQL Lab only. Port 5433 is the **replica pool**, never the leader, and DML is refused — an exploratory query from a browser has no business on the database the platform writes to. |

The dashboard **not-uber-service - analytics** is six sections over 20 charts
and five datasets: the week in numbers, money, demand that went unserved,
whether the routing held up, the city, and the fleet.

Two things the datasets encode so a chart cannot get them wrong:

- **`trip_stats_hourly` metrics sum before they divide.** It is a
  SummingMergeTree filled per node, so the same hour and zone exists on both
  shards. `avg_surge` is `sum(surge_sum) / sum(completed_trips)`, never an
  average of averages — picking "AVG" off a menu would be wrong, so the metric
  is defined for you.
- **`trip_events` holds one row per status change**, not per trip, so `trips`
  is `uniqExact(trip_id)` and each outcome is a `countIf`.

### Changing a chart

Edit the YAML and run the one-shot again:

```bash
docker compose run --rm superset-init
```

`--overwrite` means the file wins. A chart edited in the browser is **not**
written back to `assets/` — export it from Superset and commit the export, the
same way the Grafana dashboards work.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `superset` service and the `superset-init` one-shot behind the `init` profile. |
| `Dockerfile` | Superset with the ClickHouse driver installed at build time. |
| `superset_config.py` | Superset's settings: its own database, cache, row limit, proxy behaviour. |
| `init/init-superset.sh` | The four preparation steps, in order. |
| `init/register_database.py` | Adds or refreshes the ClickHouse connection inside Superset. |
| `.env.example` | Template for the untracked `.env` (image pins, secret key, logins). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone — the whole stack runs UTC. |
| `SUPERSET_IMAGE` | `apache/superset:6.1.0` | Base image. |
| `CLICKHOUSE_CONNECT_VERSION` | `1.7.2` | ClickHouse driver installed into the image. |
| `SUPERSET_SECRET_KEY` | — (required) | Signs cookies, encrypts stored passwords. Set once. |
| `SUPERSET_ADMIN_USER` / `SUPERSET_ADMIN_PASSWORD` / `SUPERSET_ADMIN_EMAIL` | `admin` / — (required) / placeholder | The Superset login. |
| `CH_HOST` / `CH_HTTP_PORT` / `CH_DATABASE` | `lb-a` / `8123` / `nus` | Where ClickHouse is, through the entry tier. |
| `CH_USER` / `CH_PASSWORD` | `nus` / — (required) | Must match `e-infra-clickhouse/.env`. |

## Standalone quickstart

```bash
docker network create nus-backbone      # once, shared by the whole stack
cp .env.example .env                    # change every secret
docker compose up -d --build
docker compose run --rm superset-init
```

## Verify

```bash
# the web server answers
docker compose exec superset curl -fsS http://localhost:8088/health

# the ClickHouse connection was registered
docker compose exec superset superset shell -c \
  "from superset.models.core import Database; print([d.database_name for d in Database.query.all()])"
```

In the browser, open <http://localhost:8088>, log in, and go to **SQL →
SQL Lab**. Pick the `ClickHouse (nus)` database and run:

```sql
SELECT hour,
       sum(completed_trips) AS trips,
       sum(revenue)         AS revenue
FROM nus.trip_stats_hourly
WHERE hour >= now() - INTERVAL 24 HOUR
GROUP BY hour
ORDER BY hour;
```

Before the app services run this returns no rows, which is correct. An
error, on the other hand, means the connection is wrong — check that
`CH_PASSWORD` matches `e-infra-clickhouse/.env` and re-run `superset-init`.

## Building dashboards that stay

Charts and dashboards built in the browser live in Superset's own database,
on the `nus-superset-home` volume. They survive restarts, but they are lost
by `docker compose down -v`. To keep one for good, export it from Superset
(**Dashboards → export**) and commit the file next to this README.

## Teardown

```bash
# keep saved dashboards
docker compose down

# remove them as well; the next start needs superset-init again
docker compose down -v
```
