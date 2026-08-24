# j-service-driver — the simulated drivers

One container manages **many** drivers, not one container per driver. A
driver is a position, a status and a destination; a single process holds
thousands of those without noticing, while a thousand containers would be a
thousand things to schedule and no closer to the truth.

## What it does, every tick

1. **Reads trip news.** It consumes `trip_lifecycle` without blocking, so a
   driver learns it has been given a trip. Where to head next comes from
   `trip:{id}:active`, the live state `dispatch-service` wrote in Redis.
2. **Moves every online driver.** Straight-line movement towards the current
   destination. A device reporting its position does not know about the road
   network — the real route belongs to the trip, and dispatch computes that
   with pgRouting.
3. **Sends one position report per online driver** to `driver_location`,
   keyed by driver id so everything about one driver stays in order.
4. **Keeps the free-driver list in Redis current.** Free drivers are added to
   the `geo:drivers:available` set with their position; busy and offline ones
   are removed, so a working driver can never be offered a second trip.

## Where each thing is written

| What | Where | Why there |
| --- | --- | --- |
| position reports | Kafka, every tick | it is a stream, and streams belong in Kafka |
| free-driver list | Redis, every tick | dispatch needs a fast "who is nearest" answer |
| last known state | PostgreSQL, every 30 s | the database keeps the answer to "where was this driver last seen", which does not need updating twenty times a minute |

Writing a position that changes every three seconds into an OLTP table would
be the classic way to overload a database that has done nothing wrong.

## Drivers move towards demand

An idle driver does not wander at random. Every minute the service reads the
current demand score of each zone from Redis and lets busy zones pull
harder, so the fleet drifts towards where riders are. Every zone keeps a
small chance, otherwise the quiet ones would empty out completely and never
recover.

That is the loop closing: `city-service` scores demand, drivers move towards
it, which changes demand.

## The volume dial

`DRIVER_TICK_SECONDS`, `DRIVER_ONLINE_SHARE` and the number of seeded
drivers together decide how loud the whole stack is. With 800 drivers, a 0.6
share and a 3-second tick, that is roughly 160 messages a second — and
halving the tick doubles the load on Kafka, on the sink, and on ClickHouse.
This is the first place to turn down if the stack is struggling, and the
main README says the same: volumes come down in config, never by removing
containers.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `driver-service` service. |
| `Dockerfile` | Two stages; runs as a normal user. |
| `driver_service/fleet.py` | What a driver is and how it moves. No Kafka, Redis or SQL in it. |
| `driver_service/__main__.py` | The loop: read news, move, report, publish the free list. |
| `.env.example` | Template for the untracked `.env`. |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `DRIVER_TICK_SECONDS` | `3.0` | How often each driver reports. The main volume dial. |
| `DRIVER_SPEED_KMH` | `25.0` | Average speed; each driver varies around it. |
| `DRIVER_ONLINE_SHARE` | `0.6` | Share of the fleet working at any moment. |
| `DRIVER_SHIFT_CHANGE_CHANCE` | `0.01` | Chance per driver per tick of starting or ending a shift. |
| `DRIVER_DB_SYNC_SECONDS` | `30.0` | How often the last known state reaches PostgreSQL. |
| `DRIVER_HOTSPOT_REFRESH_SECONDS` | `60.0` | How often drivers look up which zones are busy. |
| `CITY_*` | a box around New York | The city grid. Must match every other component. |
| `RANDOM_SEED` | `20250824` | Same seed, same behaviour — a run that repeats can be debugged. |
| `KAFKA_*`, `REDIS_*`, `PG_*` | see `.env.example` | Connections; the passwords must match pieces a, b and c. |

## Verify

```bash
# positions arriving, decoded from Avro
docker compose exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --property schema.registry.url=http://schema-registry:8081 \
  --topic driver_location --max-messages 3

# how many drivers are free right now
docker compose exec redis-1 redis-cli zcard geo:drivers:available

# the nearest free drivers to a point in the middle of the city
docker compose exec redis-1 redis-cli \
  geosearch geo:drivers:available fromlonlat -73.98 40.75 byradius 3 km asc count 5

# the database keeps the last known state, not the stream
docker compose exec pg-1 psql -U postgres -c \
  "SELECT status, count(*) FROM drivers GROUP BY status ORDER BY 2 DESC;"
```

A healthy service shows a steady message rate, a free-driver count that
moves up and down as trips start and finish, and driver statuses spread over
`offline`, `idle`, `en_route_pickup` and `on_trip`.

If `geo:drivers:available` is empty while the service logs ticks, the fleet
is busy or offline — check the status counts before suspecting Redis.
