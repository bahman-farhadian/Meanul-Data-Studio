# m-service-city — demand scores and traffic factors

Watches the live streams and answers the two questions the rest of the stack
keeps asking about the city.

| Question | Answer goes to | Read by | How often |
| --- | --- | --- | --- |
| Which zones are busy right now? | Redis `hotspot:{zone}:{period}`, and Kafka `city_hotspots` | drivers deciding where to go, dispatch deciding what to charge, the sink for history | every 30 s |
| How slow is each road right now? | PostgreSQL `segment_traffic` | pgRouting, when dispatch computes a route | every 5 min |

Two clocks, because the two are not alike: one is a small write to a cache,
the other touches every road segment inside a zone.

## How a score is worked out

Riders waiting against drivers free, per zone:

```
demand_score = waiting / (waiting + free + 1)
```

The `+1` keeps the arithmetic working in an empty zone and means one rider
with no drivers gives a high score rather than an impossible one. The score
runs from 0.0 to 1.0, and a `surge_multiplier` is derived from it with the
same shape dispatch and bootstrap use: flat at 1.0 until demand is clearly
above normal, then a gentle rise, and a hard stop at 2.5.

Where the two numbers come from:

- **waiting** — trips seen on `trip_lifecycle` at `requested` or `matched`,
  removed as soon as they reach any later status;
- **free** — drivers seen on `driver_location` reporting `idle`, removed as
  soon as they report anything else.

## How traffic factors are worked out

Cars on a trip report their speed. Averaged per zone, that gives:

```
congestion_factor = free_flow_speed / observed_speed
```

1.0 means free flowing, 2.0 means everything takes twice as long. Three
things keep it sane:

- **speeds of zero are ignored** — a car at a red light says nothing about
  the road, and enough of them would make every street look impassable;
- **the factor is capped** between 0.6 and 3.0, so one unusual car cannot
  tell the router a street is closed;
- **new readings are blended in**, not substituted. A factor that jumped
  with every reading would make routing unstable, and two identical trips a
  minute apart would get different answers.

A zone with no speed reports is skipped entirely, keeping the baseline
`h-bootstrap` seeded rather than being handed a guess.

## Everything is held in memory

The running picture of the city lives in the process and nowhere else. After
a restart it rebuilds itself within a minute from the streams. Keeping a
durable copy of something that is only ever seconds old would be more
machinery for no gain.

The Redis keys carry a six-hour lifetime — the length of one part of the day
— so a stopped `city-service` cannot leave stale scores behind for ever, and
a score can never outlive the period it describes.

## The loop this closes

`city-service` scores demand → drivers drift towards busy zones and dispatch
prices trips higher there → more cars arrive and riders are served → the
score falls. Meanwhile observed speeds change the routing costs, so the best
path itself moves with the traffic.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | The `city-service` service. |
| `Dockerfile` | Two stages; runs as a normal user. |
| `city_service/counters.py` | The running picture of a zone and the arithmetic on it. |
| `city_service/__main__.py` | The loop and the two clocks. |
| `.env.example` | Template for the untracked `.env`. |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `CITY_SCORE_SECONDS` | `30.0` | How often demand scores are published. |
| `CITY_TRAFFIC_UPDATE_MINUTES` | `5.0` | How often routing costs are refreshed. |
| `HOTSPOT_TTL_SECONDS` | `21600` | How long a score stays valid — six hours, one period. |
| `HOTSPOT_SCORE_THRESHOLD` | `0.6` | The score at which a zone counts as hot. |
| `CITY_*` (grid) | a box around New York | Must match every other component. |
| `KAFKA_*`, `REDIS_*`, `PG_*` | see `.env.example` | Connections; passwords must match pieces a, b and c. |

## Verify

```bash
# the scores as the drivers and dispatch see them
docker compose exec redis-1 redis-cli --scan --pattern 'hotspot:*' | head
docker compose exec redis-1 redis-cli get hotspot:z-03-03:morning

# a score should have a lifetime, counting down
docker compose exec redis-1 redis-cli ttl hotspot:z-03-03:morning

# the same scores as history
docker compose exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka-1:9092 \
  --property schema.registry.url=http://schema-registry:8081 \
  --topic city_hotspots --max-messages 3

# routing costs actually moving away from the seeded baseline
docker compose exec pg-1 psql -U postgres -c \
  "SELECT period, count(*) AS segments,
          round(avg(congestion_factor)::numeric, 3) AS avg_factor,
          max(updated_at) AS last_update
     FROM segment_traffic GROUP BY period ORDER BY period;"
```

If `last_update` never moves, no car has reported a usable speed — check
that `driver-service` is producing positions with `status = on_trip`.

If every zone scores 0.0, there are no waiting riders: either
`passenger-service` is not running, or `dispatch-service` is matching every
request instantly, which at low request rates is exactly what should happen.
