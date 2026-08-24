# h-bootstrap — prepares the stack, once

The last container to start and the only one meant to stop. It turns an
empty stack into one with a city, people, a street map and a week of
history, then sets a marker in Redis that releases the six services.

## What it does, in order

| Step | What happens | Safe to repeat because |
| --- | --- | --- |
| 1 | Wait for PostgreSQL, Redis and ClickHouse to answer | it only waits |
| 2 | Apply the SQL files in `migrations/` | applied files are recorded in `schema_migrations` |
| 3 | Download and import the New York street map | a valid download and an imported graph are both detected and skipped |
| 4 | Create the city zones | `ON CONFLICT DO NOTHING` |
| 5 | Create the drivers and passengers | `ON CONFLICT DO NOTHING` |
| 6 | Invent a week of trips and store them | `ON CONFLICT DO NOTHING` |
| 7 | Give road segments a starting congestion factor | `ON CONFLICT DO NOTHING` |
| 8 | Load that week into ClickHouse | skipped when `trip_events` already holds rows |
| 9 | Set `system:bootstrap:done` in Redis | setting it twice is the same as once |

**Step 9 is last on purpose.** While that key is missing every service
waits. A bootstrap that fails halfway therefore leaves a quiet stack and a
clear log, instead of services generating trips for drivers that do not
exist.

Nothing here writes to Kafka. The seeded rows reach Redis on their own:
Debezium reads them out of the database journal and `cache-updater` applies
them. That is also the proof the change-capture loop works.

## The street map

The slow step, and the one worth understanding.

1. **Download** the OpenStreetMap extract from Geofabrik, and check it
   against the `.md5` file published next to it. A half-finished download
   would produce a broken map, which is worse than none.
2. **Cut it to the city.** The published file covers New York *state*; the
   simulation only needs the box in `CITY_MIN_LAT` … `CITY_MAX_LON`. Cutting
   first makes everything after it faster and much lighter on memory.
3. **Convert** it to the XML form `osm2pgrouting` reads.
4. **Import**, which creates the `ways` and `ways_vertices_pgr` tables.
   Those are what pgRouting uses to answer "what is the best path from here
   to there".

The files live on the `nus-osm-data` volume, so a rebuilt container does not
download half a gigabyte again.

To work on something else without waiting for all this, set
`SKIP_OSM_IMPORT=true` — but routing will not work, so `dispatch-service`
cannot assign trips.

## The invented week

Two simplifications, both deliberate and both documented in the code:

- **History does not use pgRouting.** Routing 14,000 trips one by one would
  take longer than everything else together. Historical trips use the
  straight-line distance times a road factor. Live trips, from
  `dispatch-service` onwards, are routed for real.
- **Position reports are thinned out.** A real device reports every few
  seconds. A week of that is tens of millions of rows nobody reads closely,
  so a handful of points per trip is stored instead.

What is *not* simplified: the shape of the data. Trips end the way real
trips end (about seven in ten complete, the rest cancel or find no driver),
busy hours are busy, some zones are more popular than others, and the real
duration drifts away from the predicted one — which is what makes the
"slower than predicted" number on the dashboard mean something.

The random generator is seeded, so the same settings always produce the same
week. A run that can be repeated is a run that can be debugged.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `bootstrap` service, gated on the infrastructure being healthy. |
| `Dockerfile` | Two stages; the runtime image carries the map tools and the virtual environment. |
| `pyproject.toml` / `uv.lock` | Dependencies, pinned. |
| `migrations/*.sql` | The database schema, in name order. |
| `bootstrap/settings.py` | Every setting, read from the environment. |
| `bootstrap/migrate.py` | Runs the migrations that have not run yet. |
| `bootstrap/osm.py` | Download, cut, convert, import the map. |
| `bootstrap/zones.py` | The city grid. |
| `bootstrap/people.py` | Drivers and passengers. |
| `bootstrap/history.py` | The invented week, and the traffic baseline. |
| `bootstrap/warehouse.py` | Loading that week into ClickHouse. |
| `bootstrap/__main__.py` | The nine steps, in order. |
| `.env.example` | Template for the untracked `.env`. |

## Environment variables (`.env`)

The three passwords must match the components they belong to, or bootstrap
cannot connect and the stack stays waiting.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PG_PASSWORD` | — (required) | Must match `PG_SUPERUSER_PASSWORD` in `a-infra-postgres/.env`. |
| `REDIS_PASSWORD` | — (required) | Must match `b-infra-redis/.env`. |
| `CH_PASSWORD` | — (required) | Must match `e-infra-clickhouse/.env`. |
| `CITY_*` | a box around New York | Where the simulated city is, and how many zones it has. |
| `SEED_DRIVERS` / `SEED_PASSENGERS` | `800` / `5000` | How many people exist. |
| `HISTORY_DAYS` / `HISTORY_TRIPS_PER_DAY` | `7` / `2000` | How much history to invent. |
| `HISTORY_POSITIONS_PER_TRIP` | `8` | Position reports kept per historical trip. |
| `FARE_*` | `3.0` / `1.75` / `0.45` | Base fare, price per km, price per minute. Shared with `dispatch-service`. |
| `OSM_URL` / `OSM_MD5_URL` | Geofabrik New York | Where the map comes from. |
| `SKIP_OSM_IMPORT` | `false` | Skip the map entirely. Routing stops working. |
| `FORCE_RESEED` | `false` | Run the steps again on a prepared stack. |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` to see every step in detail. |

## Watching it run

```bash
# follow it; the map import is the long quiet part
docker compose logs -f bootstrap

# it exited - did it succeed?
docker inspect --format '{{.State.ExitCode}}' bootstrap
```

Exit code 0 means the stack is prepared. Anything else means it is not, and
the services are still waiting on purpose.

## Verify afterwards

```bash
# the marker that releases the services
docker compose exec redis-1 redis-cli get system:bootstrap:done

# the people and the week
docker compose exec pg-1 psql -U postgres -c \
  "SELECT (SELECT count(*) FROM drivers)    AS drivers,
          (SELECT count(*) FROM passengers) AS passengers,
          (SELECT count(*) FROM city_zones) AS zones,
          (SELECT count(*) FROM trips)      AS trips;"

# the street graph
docker compose exec pg-1 psql -U postgres -c "SELECT count(*) FROM ways;"

# the same week in the warehouse
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT count() FROM nus.trip_events"
```

Then open Grafana: the dashboard should already show a week of trips.

## Running it again

```bash
# normally does nothing, because the marker is set
docker compose up bootstrap

# actually re-run the steps (existing rows are still kept)
FORCE_RESEED=true docker compose up bootstrap
```

To start completely fresh, remove the data volumes with
`docker compose down -v` — that also drops the downloaded map, so the next
run downloads it again.
