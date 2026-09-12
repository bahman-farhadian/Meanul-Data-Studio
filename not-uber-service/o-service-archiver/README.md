# o-service-archiver — keeps `trips` down to what is still transactional

The OLTP/OLAP split this whole project is built on cuts both ways: once a
trip is over, the record of it belongs in the analytical warehouse
(`e-infra-clickhouse`'s `trip_events`), not the live database. `trip_events`
already gets that continuously — `clickhouse-sink` consumes the same
`trip_lifecycle` events every trip publishes as it happens, not a batch job
this service triggers. This is the other half of that split: without it,
`trips` grows for as long as the stack runs live, no different from why
`driver_positions`/`driver_location` needed their own retention cut.

## What it does

Every `ARCHIVER_TICK_MINUTES`, deletes every trip whose `ended_at` is older
than `ARCHIVER_RETENTION_HOURS` — in batches of `ARCHIVER_BATCH_SIZE`, up to
`ARCHIVER_MAX_BATCHES_PER_TICK` batches per tick, so a real backlog (the
first run after deploying this, or a stack that has been live a long time)
takes more ticks rather than one tick running forever.

`ended_at` is only ever set for a trip that reached a terminal status
(`completed`, `cancelled_by_passenger`, `cancelled_by_driver`,
`no_driver_found` — l-service-dispatch's own `UPDATE`), so `ended_at IS NOT
NULL` already means "over" — a trip still `requested`/`matched`/`in_progress`
is never touched no matter how old its `requested_at` is.

`trip_ratings` references `trips` with no `ON DELETE CASCADE`, so a pruned
trip's ratings are deleted first, in the same transaction as the trip.

## What is lost on prune

`trip_events` does not carry the trip's route geometry (`trips.route`, a
Postgres-only PostGIS column — this is most of what makes `trips` grow at
all) or a few other operational fields (`pickup_point`/`dropoff_point` as
geometry, `requested_vehicle_type`, `attributes`). Deliberately: those matter
while a trip is recent — inspecting a route on a map, debugging a specific
request — not months later, and carrying them into ClickHouse would only
move the same storage cost somewhere else instead of removing it. Everything
the historical/analytical side (Superset, the daily rollups) actually reads
already survives in `trip_events`.

## Postgres only

No Kafka, no Redis beyond the standard bootstrap-done check every service
waits on. There is nothing here for another service to consume — this
deletes rows, it does not announce anything about them.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `archiver-service` service. |
| `Dockerfile` | Two stages; runs as a normal user. |
| `archiver_service/__main__.py` | The loop and the prune query. |
| `.env.example` | Template for the untracked `.env`. |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ARCHIVER_TICK_MINUTES` | `15.0` | How often a prune pass runs. |
| `ARCHIVER_RETENTION_HOURS` | `24` | How long a finished trip stays in Postgres. |
| `ARCHIVER_BATCH_SIZE` | `5000` | Trips deleted per batch. |
| `ARCHIVER_MAX_BATCHES_PER_TICK` | `20` | Ceiling on one tick's own work. |
| `PG_*` | see `.env.example` | Connection; password must match piece a. |

## Verify

```bash
# how many trips are old enough to prune right now, and how many never will
# be until they finish
docker compose exec pg-1 psql -U postgres -d nus -c \
  "SELECT
     count(*) FILTER (WHERE ended_at IS NOT NULL AND ended_at < now() - interval '24 hours') AS prunable,
     count(*) FILTER (WHERE ended_at IS NULL) AS still_active,
     count(*) AS total
   FROM trips;"

# confirm nothing prunable is left after a tick, and nothing under
# retention or still active got touched
docker compose logs archiver-service --since 20m
```

If `prunable` never reaches zero, check `ARCHIVER_MAX_BATCHES_PER_TICK` isn't
too low for the real backlog, or that the service is actually running
(`docker compose ps archiver-service`).
