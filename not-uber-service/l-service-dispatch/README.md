# l-service-dispatch — matching, routing and pricing

Takes ride requests, finds a driver, works out the real route and the price,
and then sees the trip through to its end.

## The only owner of trip status

Every status change of every trip is decided here:

```
requested -> matched -> accepted -> en_route_pickup -> in_progress -> completed
```

plus the three ways a trip ends early — `cancelled_by_driver`,
`cancelled_by_passenger`, and `no_driver_found` when nobody could be given
the trip at all.

One owner means one place to look when a trip is stuck, and no chance of two
services disagreeing about what state a trip is in. `driver-service` and
`passenger-service` learn about changes by listening to `trip_lifecycle`;
neither of them ever decides one.

## What happens to one request

1. **Find a driver.** Redis keeps free drivers in a geo set that
   `driver-service` refreshes every few seconds, so this is one fast lookup
   for "who is nearest", not a query across the fleet. Nothing here reads
   PostgreSQL to find a driver.
2. **Route it.** pgRouting walks the imported street network. The cost of
   each road segment is its travel time multiplied by how congested it is at
   this time of day, so the same two points get a different best path at
   8am than at 3am. This is the expensive step — roughly 50 to 150
   milliseconds per trip, and the slowest thing in the whole pipeline.
3. **Price it.**
   `fare = (base + per_km x kilometres + per_minute x minutes) x surge`,
   with the surge multiplier taken from the pickup zone's demand score. The
   estimate uses the predicted duration; the final fare, at the end, uses the
   real one — so traffic actually costs money.
4. **Announce it**, write the row, and take the driver out of the free list
   at once so nobody can be given two trips.

## When nobody can be served

Two cases end the same way, as `no_driver_found`:

- no free driver within `DISPATCH_SEARCH_RADIUS_KM`;
- the two points are not connected in the imported map, usually because one
  of them falls outside the imported area.

Both are recorded and announced rather than dropped. "We could not serve
this" is a number worth having: it is what says the fleet is too small at
this hour, or the map is too small for the city box.

## Live state in Redis

While a trip runs, `trip:{id}:active` holds where the car is now, where it is
going, and what the route predicted. `driver-service` reads it to know where
to head; `passenger-service` reads it to place the rider's phone;
`clickhouse-sink` reads it to compare predicted against actual. It carries a
lifetime as a safety net, and a finished trip deletes its own entry.

This is not the same as `trip:{id}`, which `cache-updater` writes from the
database. That one is the record; this one is the live state.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `dispatch-service` service. |
| `Dockerfile` | Two stages; runs as a normal user. |
| `dispatch_service/routing.py` | The pgRouting query, with traffic-weighted costs. |
| `dispatch_service/pricing.py` | Surge lookup and the fare formula. |
| `dispatch_service/trips.py` | When a trip changes status. No Kafka, Redis or SQL in it. |
| `dispatch_service/__main__.py` | The loop: match, route, price, announce, advance. |
| `.env.example` | Template for the untracked `.env`. |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `FARE_BASE` / `FARE_PER_KM` / `FARE_PER_MINUTE` | `3.0` / `1.75` / `0.45` | The price formula. Must match `h-bootstrap`, so live trips are priced like the history. |
| `DISPATCH_TICK_SECONDS` | `1.0` | How often it looks for new work. |
| `DISPATCH_SEARCH_RADIUS_KM` | `5.0` | How far to look for a free driver. Wider finds a driver more often and makes riders wait longer. |
| `DISPATCH_MAX_REQUESTS_PER_TICK` | `50` | Ceiling per tick, so a burst cannot turn one tick into a minute of routing. |
| `CANCEL_BY_DRIVER_CHANCE` / `CANCEL_BY_PASSENGER_CHANCE` | `0.06` / `0.07` | How often trips fall apart after matching. |
| `TRIP_ACTIVE_TTL_SECONDS` | `7200` | Safety net for the live trip entry. |
| `CITY_*` | a box around New York | The city grid. Must match every other component. |
| `KAFKA_*`, `REDIS_*`, `PG_*` | see `.env.example` | Connections; passwords must match pieces a, b and c. |

## Verify

```bash
# trips moving through their statuses in the last ten minutes
docker compose exec pg-1 psql -U postgres -c \
  "SELECT status, count(*) FROM trips
    WHERE requested_at > now() - interval '10 minutes'
    GROUP BY status ORDER BY 2 DESC;"

# routes and prices are actually being stored
docker compose exec pg-1 psql -U postgres -c \
  "SELECT trip_id, round(route_km::numeric, 2) AS km, predicted_duration_s,
          surge_multiplier, fare_estimate, fare_final
     FROM trips WHERE driver_id IS NOT NULL
    ORDER BY requested_at DESC LIMIT 5;"

# the lifecycle stream
docker compose exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --property schema.registry.url=http://schema-registry:8081 \
  --topic trip_lifecycle --max-messages 5

# is it keeping up with the requests?
docker compose exec kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka-1:9092 --describe --group dispatch-service
```

Healthy looks like: a completed share somewhere near 70 percent, a small
number of `no_driver_found`, routes with sensible kilometres, and consumer
lag that stays low.

## When it falls behind

Growing lag on `dispatch-service` almost always means routing is the
bottleneck, because it is the one heavy step. In order of preference:

1. lower `TRIP_REQUESTS_PER_MINUTE` in `k-service-passenger` — fewer routing
   queries is the real fix;
2. check that `segment_traffic` has rows, since a missing traffic table does
   not slow routing down but a missing `ways` table stops it entirely;
3. look at PostgreSQL itself: `pgr_dijkstra` runs on the read replicas
   through port 5433, so a lagging replica shows up here first.

Adding a second dispatch container would work — the topic has three
partitions — but the main README's advice holds: turn the volume down in
config before adding containers.
