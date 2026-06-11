# Meanul Data Studio

## 1. About Meanul Data Studio

Meanul Data Studio is a framework for simulating the **full backend data
lifecycle of an online platform**, end to end, packaged as a set of Docker
containers and run via a single `docker-compose.yml` on a single host.

Each "version" of the studio applies the same architectural pattern to a
different product domain — for example a cab/ride-hailing platform, a
music/video streaming platform (Spotify/Netflix-like), a flight-booking /
airport platform, and so on. The studio is designed to host **multiple such
versions over time**; the pattern itself is domain-agnostic:

- **Synthetic data generation** — All activity (users, drivers, listeners,
  trips, plays, bookings, etc.) is produced by services built with the
  [Faker](https://faker.readthedocs.io/) library, generating realistic,
  internally-consistent records rather than arbitrary random values.
  Generation pacing (volume, rate, time-of-day/weekday/seasonal weighting)
  is config-driven.
- **OLTP layer (PostgreSQL)** — The single source of truth for operational
  state (e.g. driver/passenger/trip tables): a solid, transactional
  database that generators write to. Semi-structured / JSON-like payloads
  are stored natively in PostgreSQL **`JSONB`** columns — no separate
  document database (e.g. MongoDB) is introduced for them.
- **Cache layer (Redis, Sentinel-managed)** — Sits in front of the OLTP
  layer as the **read path for everything else**. ClickHouse-feeding
  consumers and other mimic services must never query the PostgreSQL OLTP
  cluster directly for joins/lookups — they read from Redis instead. When
  data in PostgreSQL changes, the change is captured via **CDC (Debezium
  over Kafka)** and applied to the cache, keeping Redis consistent with
  the source of truth.
- **Streaming backbone (Kafka cluster, KRaft mode)** — A sharded,
  multi-broker Kafka cluster running in KRaft mode (no ZooKeeper).
  Generators and/or the OLTP layer publish events onto Kafka topics.
- **OLAP layer (ClickHouse, ClickHouse Keeper)** — A sharded/replicated
  ClickHouse cluster fed from Kafka, coordinated via a ClickHouse Keeper
  ensemble (no ZooKeeper dependency).
- **Dashboards** — [Grafana](https://grafana.com/) for live/operational
  views and [Apache Superset](https://superset.apache.org/) for analytical
  BI dashboards, both backed by ClickHouse.

Every component, including the Python generator services, runs inside its
own Docker container — there is no host-level virtual environment; any
Python `venv`s live entirely inside the container images.

```mermaid
graph LR
    subgraph Generators ["Synthetic Data Generators (Faker, in containers)"]
        G1["Generator Service A"]
        G2["Generator Service B"]
    end

    subgraph OLTP ["PostgreSQL (OLTP, source of truth)"]
        PG[("PostgreSQL")]
    end

    subgraph Cache ["Redis (Sentinel-managed)"]
        R[("Redis Cache")]
    end

    subgraph Streaming ["Kafka Cluster (KRaft, sharded)"]
        K[("Kafka Brokers")]
    end

    subgraph OLAP ["ClickHouse Cluster (ClickHouse Keeper)"]
        CH[("ClickHouse Shards / Replicas")]
        KE[("ClickHouse Keeper Ensemble")]
    end

    subgraph Dashboards ["Dashboards"]
        GF["Grafana"]
        SS["Superset"]
    end

    G1 -->|"writes: profiles, trips, state"| PG
    G2 -->|"writes: profiles, trips, state"| PG
    PG -.->|"change notification / cache sync"| R

    G1 -->|"publish events"| K
    G2 -->|"publish events"| K

    R -.->|"lookups and joins for consumers"| K
    K -->|"consumers"| CH

    CH <-.->|"coordination"| KE
    CH --> GF
    CH --> SS
```

## 2. Version 1 — Cab / Ride-Hailing Platform

Version 1 simulates an online cab/ride-hailing platform: drivers and
passengers, trip requests, real-time location streaming during a trip, real
routes over the NYC street network, dynamic demand "hotspots", and the
analytics built on top of all of the above.

### 2.1 Services

The platform is split into one **one-shot init service** and several
**long-running services**, each its own container:

| Service | Type | Responsibility |
| --- | --- | --- |
| `bootstrap` | one-shot | Runs DB migrations, imports the NYC OSM road network into PostgreSQL and builds the pgRouting topology, seeds initial reference data (drivers, passengers, city zones), and generates **one week of historical mock activity** (trips, locations, per-segment traffic) so the ecosystem starts with a realistic baseline. Exits and is removed when done. |
| `driver-service` | long-running | Simulates the pool of active drivers (not one container per driver — one service internally manages many simulated drivers). Produces driver status/location activity. |
| `passenger-service` | long-running | Simulates the pool of riders. Produces trip requests and rider device location streams. |
| `dispatch-service` | long-running | Matches trip requests from `passenger-service` to available drivers, computes the route via pgRouting, and assigns the trip. |
| `city-service` | long-running | Watches live trip/location traffic, computes per-zone demand "hotspot" scores and per-road-segment congestion factors (refreshing `segment_traffic` used by routing), and publishes hotspots so drivers can be guided toward demand. |
| `clickhouse-sink` | long-running | Consumes Kafka topics, enriches events via Redis, and writes into the ClickHouse cluster for analytics/dashboards. |
| `cache-updater` | long-running | Consumes the Debezium CDC topics and applies PostgreSQL changes to Redis, keeping the cache in sync with the source of truth. |

Note: in the real world, driver-side and rider-side telemetry are **not**
1:1 mirrors of each other (different devices, different sampling rates,
different failure modes) — `driver-service` and `passenger-service` are
independent generators that happen to refer to the same trips, not a single
simulation duplicated into two streams.

#### Container startup procedure

`docker-compose up` brings the stack up in a strict order, enforced through
healthchecks and `depends_on: condition: service_healthy`:

1. **Infrastructure clusters** start first: PostgreSQL (Patroni-managed),
   Redis + Sentinel, Kafka (KRaft) + Debezium Connect, ClickHouse + Keeper,
   Grafana, Superset — each with a healthcheck.
2. **Long-running app services** (`driver-service`, `passenger-service`,
   `dispatch-service`, `city-service`, `clickhouse-sink`, `cache-updater`)
   start once the infrastructure they depend on is healthy, and wait in
   standby until the data layer is initialized.
3. **`bootstrap` is the last container to come up**, gated on every other
   service being healthy. It runs migrations, imports the NYC OSM extract
   and builds the pgRouting topology, seeds initial drivers/passengers/city
   zones, generates one week of historical mock activity (including
   per-segment traffic baselines), preloads the cache, then exits
   successfully and is removed.
4. Once `bootstrap` has completed, the standby app services detect the
   initialized data layer and begin generating live activity; from this
   point the whole ecosystem runs purely from its configuration.

### 2.2 High-level overview (HLD)

```mermaid
graph TB
    BOOT["bootstrap<br/>(one-shot, starts last, removed after success)"]
    DRV["driver-service"]
    PSG["passenger-service"]
    DISP["dispatch-service"]
    CITY["city-service"]
    SINK["clickhouse-sink"]
    CUPD["cache-updater"]

    PG[("PostgreSQL Cluster<br/>Patroni: primary + 2 replicas<br/>drivers / passengers / trips / NYC road network")]
    REDIS[("Redis Sentinel<br/>profiles, active trips, hotspots, geo index")]
    KAFKA[("Kafka Cluster<br/>KRaft, sharded")]
    CH[("ClickHouse Cluster")]
    GRAF["Grafana"]
    SUPER["Superset"]

    BOOT -->|"migrations, OSM import + pgRouting topology, seed + 1 week of history"| PG
    BOOT -->|"preload cache"| REDIS

    DRV -->|"status / location writes"| PG
    PSG -->|"trip request writes"| PG
    PG -.->|"CDC via Debezium"| KAFKA
    KAFKA -.->|"cdc topics"| CUPD
    CUPD -->|"apply changes"| REDIS

    REDIS -.->|"profiles, active trips, hotspot scores"| DRV
    REDIS -.->|"profiles, active trips"| PSG

    DRV -->|"stream: driver_location"| KAFKA
    PSG -->|"stream: rider_location, trip_requests"| KAFKA

    KAFKA -->|"trip_requests"| DISP
    REDIS -.->|"geo lookup: nearby available drivers"| DISP
    DISP -->|"route calc (pgr_dijkstra) + assign trip"| PG
    DISP -->|"update active-trip cache"| REDIS
    DISP -->|"publish: trip_lifecycle"| KAFKA

    KAFKA -->|"driver_location, rider_location, trip_lifecycle"| CITY
    CITY -->|"hotspot score per zone and period, TTL 6h"| REDIS
    CITY -->|"refresh segment_traffic congestion factors"| PG
    CITY -->|"publish: city_hotspots"| KAFKA

    KAFKA --> SINK
    REDIS -.->|"enrichment: hotspot flags, predicted durations"| SINK
    SINK --> CH
    CH --> GRAF
    CH --> SUPER
```

### 2.3 PostgreSQL cluster (HLD)

PostgreSQL is the system of truth for all transactional/operational state
and the NYC road network graph. It runs as a real cluster — **one primary +
two streaming replicas with automatic failover managed by Patroni** (backed
by an etcd DCS) — and relies on three pillars:

- **[PostGIS](https://postgis.net/)** — the extension that stores the NYC
  map: road geometries, pickup/drop-off points, and route linestrings live
  in `geometry` columns with GiST spatial indexes.
- **[pgRouting](https://pgrouting.org/)** — the extension that turns the
  map into a routable graph and finds the best path for each trip
  (see [2.7](#27-nyc-road-network--routing) for the traffic-aware cost
  model).
- **`JSONB`** — all JSON-like / semi-structured payloads (device metadata,
  event payloads, flexible trip attributes) are stored in native `JSONB`
  columns with GIN indexes where needed — PostgreSQL covers the
  document-store role, so no MongoDB is part of the stack.

```mermaid
graph TB
    subgraph PGCluster ["PostgreSQL Cluster (Patroni + etcd)"]
        PRIM[("Primary")]
        REP1[("Replica 1")]
        REP2[("Replica 2")]
        ETCD[("etcd DCS")]
    end

    PRIM -->|"streaming replication"| REP1
    PRIM -->|"streaming replication"| REP2
    ETCD -.->|"leader election / failover"| PRIM
```

```mermaid
graph TB
    BOOT["bootstrap"]
    DRV["driver-service"]
    PSG["passenger-service"]
    DISP["dispatch-service"]

    SCHEMA[("schema: drivers, passengers, trips, city_zones")]
    ROADS[("ways / ways_vertices_pgr<br/>NYC road network")]
    REDIS[("Redis")]

    BOOT -->|"1: run migrations"| SCHEMA
    BOOT -->|"2: import OSM extract, build pgRouting topology"| ROADS
    BOOT -->|"3: seed reference rows + 1 week of historical activity"| SCHEMA

    DRV -->|"insert / update driver status"| SCHEMA
    PSG -->|"insert trip request, status = requested"| SCHEMA
    DISP -->|"pgr_dijkstra / pgr_astar: pickup to drop-off"| ROADS
    DISP -->|"update trip: assign driver, route, status"| SCHEMA

    SCHEMA -.->|"logical replication slot"| DBZ["Debezium CDC"]
    DBZ -.->|"Kafka cdc topics, applied by cache-updater"| REDIS
```

Core tables (conceptual):

- `drivers` — driver profile + current status (offline/idle/en-route/on-trip);
  device/vehicle metadata in `JSONB`.
- `passengers` — passenger/rider profile; preferences/device metadata in
  `JSONB`.
- `trips` — pickup point, drop-off point (PostGIS points), assigned driver,
  computed route geometry (PostGIS linestring), predicted duration, status,
  timestamps; flexible attributes in `JSONB`.
- `city_zones` — NYC zone/grid definitions used for hotspot aggregation.
- `segment_traffic` — per-road-segment congestion factors: baseline from the
  bootstrap-seeded historical week, continuously refreshed by `city-service`
  from live streams; consumed by pgRouting as edge-cost multipliers.
- `ways` / `ways_vertices_pgr` — pgRouting topology built from the NYC OSM
  extract (see [2.7](#27-nyc-road-network--routing)).

### 2.4 Redis cluster (HLD)

Redis runs as a Sentinel-managed primary/replica set and is the **only**
read path for cached reference and hot-path data — generators, dispatch,
the city service, and the ClickHouse sink read from here, never directly
from PostgreSQL.

```mermaid
graph TB
    subgraph Sentinel ["Redis Sentinel"]
        S1["Sentinel 1"]
        S2["Sentinel 2"]
        S3["Sentinel 3"]
    end

    M[("Redis Primary")]
    R1[("Replica 1")]
    R2[("Replica 2")]

    S1 -.->|"monitor / failover"| M
    S2 -.->|"monitor / failover"| M
    S3 -.->|"monitor / failover"| M
    M -->|"replication"| R1
    M -->|"replication"| R2
```

Key spaces (conceptual):

| Key pattern | Written by | Read by | Notes |
| --- | --- | --- | --- |
| `driver:{id}` | `cache-updater` (CDC) | `driver-service`, `dispatch-service`, `clickhouse-sink` | profile + current status |
| `passenger:{id}` | `cache-updater` (CDC) | `passenger-service`, `dispatch-service`, `clickhouse-sink` | profile |
| `trip:{id}:active` | `dispatch-service` | `driver-service`, `passenger-service`, `clickhouse-sink` | active-trip state incl. route + predicted duration |
| `geo:drivers:available` | `driver-service` | `dispatch-service` | Redis GEO set for nearest-driver lookup |
| `hotspot:{zone}:{period}` | `city-service` | `driver-service`, `clickhouse-sink` | demand score, **TTL = 6h** (24h split into 4 periods) |

### 2.5 Kafka cluster (HLD)

Kafka runs as a sharded, multi-broker cluster in **KRaft mode** (combined
broker/controller nodes, no ZooKeeper).

```mermaid
graph TB
    subgraph Kafka ["Kafka Cluster (KRaft)"]
        B1["Broker 1<br/>broker + controller"]
        B2["Broker 2<br/>broker + controller"]
        B3["Broker 3<br/>broker + controller"]
    end

    T1[["driver_location<br/>(N partitions)"]]
    T2[["rider_location<br/>(N partitions)"]]
    T3[["trip_requests<br/>(N partitions)"]]
    T4[["trip_lifecycle<br/>(N partitions)"]]
    T5[["city_hotspots<br/>(N partitions)"]]

    B1 --- T1
    B1 --- T2
    B2 --- T3
    B2 --- T4
    B3 --- T5
```

| Topic | Producer | Consumer(s) |
| --- | --- | --- |
| `driver_location` | `driver-service` | `city-service`, `clickhouse-sink` |
| `rider_location` | `passenger-service` | `city-service`, `clickhouse-sink` |
| `trip_requests` | `passenger-service` | `dispatch-service`, `clickhouse-sink` |
| `trip_lifecycle` | `dispatch-service` | `city-service`, `clickhouse-sink` |
| `city_hotspots` | `city-service` | `clickhouse-sink` |
| `cdc.*` (per table) | Debezium Connect (from PostgreSQL WAL) | `cache-updater` |

### 2.6 ClickHouse cluster (HLD)

ClickHouse runs as **2 shards x 2 replicas** coordinated by a **3-node
ClickHouse Keeper ensemble** (no ZooKeeper) — sharding and replication are
both demonstrated at a footprint that fits a single host. `clickhouse-sink`
consumes every Kafka topic above and writes into denormalized analytics
tables that back Grafana (live/ops) and Superset (BI).

**Relationship with the Redis cluster:** before inserting, the sink
enriches events using Redis-cached state — for example, when a completed
trip arrives it looks up `hotspot:{zone}:{period}` to mark whether it was a
**hotspot trip**, and compares the actual duration against the predicted
duration cached on `trip:{id}:active` to flag trips that **took longer than
predicted** so Grafana can surface them. This keeps all such lookups off
the PostgreSQL OLTP cluster, per the cache-first rule in Section 1.

```mermaid
graph TB
    KAFKA[("Kafka Cluster")]
    REDIS[("Redis Cluster")]
    SINK["clickhouse-sink"]

    subgraph OLAP ["ClickHouse Cluster"]
        CH1[("Shard 1<br/>Replicas 1 / 2")]
        CH2[("Shard 2<br/>Replicas 1 / 2")]
        KEEPER[("ClickHouse Keeper Ensemble")]
    end

    GRAF["Grafana"]
    SUPER["Superset"]

    KAFKA -->|"all topics"| SINK
    REDIS -.->|"enrichment: hotspot scores, predicted durations"| SINK

    SINK -->|"live_driver_positions, live_rider_positions"| CH1
    SINK -->|"trip_events (hotspot flag, duration delta), hotspot_history"| CH2

    CH1 <-.->|"coordination / replication"| KEEPER
    CH2 <-.->|"coordination / replication"| KEEPER

    CH1 --> GRAF
    CH2 --> GRAF
    CH1 --> SUPER
    CH2 --> SUPER
```

### 2.7 NYC road network & routing

The PostgreSQL OLTP database holds the NYC street network as a routable
graph, used to compute a real route (pickup -> drop-off) for every trip.
The map is stored by the **PostGIS** extension and routed by the
**pgRouting** extension:

- **Source data**: an OpenStreetMap extract for New York City (e.g. via
  Geofabrik or the OSM Overpass API), which provides accurate, freely
  licensed street geometry and metadata for the full road network.
- **Loading**: the `bootstrap` service imports the OSM extract into
  PostgreSQL/**PostGIS** and converts it into a routable topology using
  `osm2pgrouting` (or `osm2pgsql` + pgRouting's topology functions),
  producing the standard pgRouting `ways` / `ways_vertices_pgr` tables.
- **Traffic-aware best path**: routing does not use raw geometric distance
  alone. Each road segment's cost is its base travel time (length /
  segment speed) multiplied by a **congestion factor** from the
  `segment_traffic` table. The baseline factors come from the week of
  historical activity seeded by `bootstrap`; from then on `city-service`
  continuously recomputes them from the live `driver_location` /
  `rider_location` streams. The same trip can therefore get a different
  "best" route at rush hour than at 3 AM.
- **Routing**: for each trip, `dispatch-service` calls pgRouting
  (`pgr_dijkstra` or `pgr_astar`) with the traffic-weighted edge costs to
  compute the best path between the pickup and drop-off vertices; the
  resulting route geometry (PostGIS linestring) and predicted duration are
  stored on the trip record. The predicted duration is what the
  `clickhouse-sink` later compares against actual duration to flag
  overrunning trips.
- **Indexing**: spatial indexes (GiST on geometry columns) and pgRouting's
  vertex/edge indexes are applied so route lookups remain fast as trip
  volume grows.

### 2.8 Project structure

```
meanul-data-studio/
├── docker-compose.yml      # single-host deployment of the full stack
├── README.md
├── LICENSE
├── .gitignore
├── bootstrap/              # one-shot init service (starts last, then removed)
│   ├── migrations/         # SQL schema migrations
│   ├── osm/                # NYC OSM extract download + osm2pgrouting setup
│   └── seed/               # initial drivers/passengers/city_zones data
├── services/               # long-running Faker-based app services
│   ├── driver-service/
│   ├── passenger-service/
│   ├── dispatch-service/
│   ├── city-service/
│   ├── clickhouse-sink/
│   └── cache-updater/      # CDC topics -> Redis
├── infra/                  # per-cluster configuration
│   ├── postgres/           # Patroni (primary + 2 replicas) + etcd config
│   ├── redis/              # Sentinel config
│   ├── kafka/              # KRaft broker config, topic definitions
│   ├── debezium/           # Kafka Connect + PostgreSQL CDC connector
│   ├── clickhouse/         # cluster + Keeper config, table DDL
│   ├── grafana/            # provisioned dashboards/datasources
│   └── superset/           # provisioned datasets/dashboards
└── docs/
    ├── clickhouse-cluster-design.md   # early cluster topology notes
    └── sketch/                        # superseded first-draft generator (reference only)
```

### 2.9 Showcase

_Screenshots of the running system (PostgreSQL data, Grafana dashboards,
Superset dashboards, etc.) will be added here._
