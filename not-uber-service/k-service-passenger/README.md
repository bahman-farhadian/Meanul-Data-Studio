# k-service-passenger — the simulated riders

Creates ride requests, and sends a position report for every rider who is
currently in a car.

## Not a mirror of driver-service

The two look similar and are deliberately separate. A phone in a pocket and
a device on a windscreen report at different rates, with different accuracy,
and fail in different ways. Producing both streams from one simulation would
make the data look tidier than reality and quietly remove the very
differences the analytics are supposed to show.

## A request is written twice, on purpose

Each new request becomes:

- a **row** in the `trips` table, status `requested` — the record that the
  request exists;
- a **message** on `trip_requests` — how `dispatch-service` hears about it.

One is state, the other is news. The row is written first: if the process
died between the two, dispatch would never hear about a trip that exists,
which shows up as a stuck `requested` row and can be found. The other order
would announce a trip that was never recorded, which cannot.

## Demand has a shape

Two things stop this from being a random number generator:

- **Time of day.** Requests follow the same hourly curve `h-bootstrap` used
  for the seeded week — two peaks and a quiet night — so live traffic
  continues the history instead of contradicting it.
- **Place.** Each zone has a popularity derived from its own name, so it is
  the same on every service and after every restart. A city where the busy
  areas moved whenever a container restarted would make hotspots meaningless.

The number of requests in a tick is drawn around the expected value rather
than being exactly it, so requests arrive unevenly, the way they do in life.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `passenger-service` service. |
| `Dockerfile` | Two stages; runs as a normal user. |
| `passenger_service/demand.py` | The hourly curve and the zone popularity. |
| `passenger_service/__main__.py` | The loop: request, follow trip news, report positions. |
| `.env.example` | Template for the untracked `.env`. |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRIP_REQUESTS_PER_MINUTE` | `40.0` | Average for an ordinary hour. Busy hours roughly double it. |
| `PASSENGER_TICK_SECONDS` | `5.0` | How often the service creates requests and reports positions. |
| `CITY_*` | a box around New York | The city grid. Must match every other component. |
| `RANDOM_SEED` | `20250824` | Same seed, same stream of requests. |
| `KAFKA_*`, `REDIS_*`, `PG_*` | see `.env.example` | Connections; passwords must match pieces a, b and c. |

**This is the second volume dial of the stack**, after `driver-service`'s
tick. Every request becomes a routing calculation in `dispatch-service`,
which is the most expensive single step anywhere in the pipeline — the main
README puts it at 50 to 150 milliseconds each. Raising this rate is what
will make dispatch fall behind first.

## Verify

```bash
# requests arriving
docker compose exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --property schema.registry.url=http://schema-registry:8081 \
  --topic trip_requests --max-messages 3

# rows appearing, and moving out of 'requested' as dispatch picks them up
docker compose exec pg-1 psql -U postgres -c \
  "SELECT status, count(*) FROM trips
    WHERE requested_at > now() - interval '10 minutes'
    GROUP BY status ORDER BY 2 DESC;"

# rider positions, which only exist while trips are in progress
docker compose exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --property schema.registry.url=http://schema-registry:8081 \
  --topic rider_location --max-messages 3
```

A growing pile of rows stuck at `requested` means `dispatch-service` is not
keeping up, or has no free drivers to give them — check
`geo:drivers:available` in Redis before suspecting this service.
