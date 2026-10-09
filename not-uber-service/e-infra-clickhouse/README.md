# e-infra-clickhouse — one ClickHouse

One server, `ch-s1r1` / `nus-ch-s1r1`. `clickhouse-sink` writes events
here, `h-bootstrap` loads history here, and Grafana and Superset read
here.

Why a second copy on this host is not a second machine:
[`clickhouse-cluster-design.md`](clickhouse-cluster-design.md).

## Tables

The query name is the table. `nus.driver_positions` and
`nus.trip_events` store the rows. Engines:

| Engine | Used for |
| --- | --- |
| `MergeTree` | Raw streams: positions, trip events, hotspots, offers, segment traffic. |
| `SummingMergeTree` | Additive rollups such as `trip_stats_hourly`. |
| `AggregatingMergeTree` | Rollups that need a state combinator. |
| `ReplacingMergeTree(event_time)` | `trip_facts`, collapsed on `event_time` at merge time. |

Read and write those names. There is no `*_local` suffix.

`SummingMergeTree` adds matching rows when it merges, not on every
insert. A query still aggregates:

```sql
SELECT hour,
       sum(completed_trips) AS trips,
       sum(revenue) AS revenue,
       sum(surge_sum) / sum(completed_trips) AS avg_surge
FROM nus.trip_stats_hourly
WHERE hour >= now() - INTERVAL 24 HOUR
GROUP BY hour
ORDER BY hour;
```

`trip_events` carries columns Kafka does not: the pickup zone's demand
score, whether the trip was a hotspot trip, and how far the real
duration drifted from the prediction. `clickhouse-sink` fills those
from Redis.

| Table | Holds |
| --- | --- |
| `driver_positions` | Driver position reports. |
| `rider_positions` | Rider position reports. |
| `trip_events` | Trip status changes, enriched by the sink. |
| `hotspot_history` | Demand score of each zone over time. |
| `trip_stats_hourly` | Completed trips summed per hour and pickup zone. |

TTL on each table is what bounds disk. The full list is `ddl/`.

## Memory

`max_server_memory_usage` is 6.5 GB against an 8 GB container limit.
One query may use at most 4 GB. A refused query says "memory limit
exceeded". An OOM kill does not.

## Ports

HAProxy **8123** (HTTP) and **9000** (native) both go to
`nus-ch-s1r1`. Clients use
`CH_HOST=nus-lb-a`.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `ch-s1r1`, plus `ch-ddl-init`. |
| `config/clickhouse/config.d/memory.xml` | Server memory ceiling. Mounted. |
| `config/clickhouse/users.d/profiles.xml` | Per-query limits. Mounted. |
| `ddl/*.sql` | Tables, in name order. `IF NOT EXISTS`. |
| `ddl/apply-ddl.sh` | Applies those files. Safe to re-run. |
| `clickhouse-cluster-design.md` | Why this is one process. |
| `.env.example` | Image pin and login. |

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone. |
| `CH_SERVER_IMAGE` | `clickhouse/clickhouse-server:26.8.2.7` | Server image, also used by the DDL one-shot. |
| `CH_USER` | `nus` | Login every client uses. |
| `CH_PASSWORD` | required | Created by the image on first start. |
| `CH_CPUS` | `1.3` | CPU ceiling. Set in the root `.env`. |
| `CH_MEM` | `8g` | Memory ceiling. |

## Verify

```bash
make verify-ch
```

That lists `name, engine` from `system.tables` for the `nus` database
where the engine is a MergeTree. The line under it is: one ClickHouse.
The engines are MergeTree.

From this directory alone:

```bash
docker network create nus-backbone
cp .env.example .env
docker compose up -d
docker compose run --rm ch-ddl-init
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT name, engine FROM system.tables WHERE database='nus' AND engine LIKE '%MergeTree' ORDER BY name"
```

## Adding a table

Add a numbered file under `ddl/` and run:

```bash
docker compose run --rm ch-ddl-init
```

## Teardown

```bash
docker compose down
docker compose down -v
```

After a volume wipe, run `ch-ddl-init` again on the next start.
