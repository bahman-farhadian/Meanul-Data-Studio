# e-infra-clickhouse — the analytics database

Four ClickHouse nodes as **2 shards x 2 replicas**, plus three
**ClickHouse Keeper** nodes. `clickhouse-sink` writes every event here,
`h-bootstrap` loads the historical week here, and Grafana and Superset read
from here.

The design notes — why this shape, what fails when a node is lost — are in
[`clickhouse-cluster-design.md`](clickhouse-cluster-design.md). This file is
about running it.

## The two words to know

- **Shard**: one half of the data. A row belongs to exactly one shard.
- **Replica**: a second copy of a shard, on a different container.

So the data is split in two and each half is stored twice. Any single node
can be lost while queries keep working and no data is lost.

**Keeper** is the small service the nodes use to agree with each other:
which replica has the newest data, who may merge which part, and where a
`CREATE TABLE ... ON CLUSTER` has already been applied. It replaces
ZooKeeper, so the stack has one less thing to run. Three Keeper nodes means
one may be down and the remaining two still form a majority.

## Local tables and Distributed tables

Every stream has two tables:

| Name | What it is |
| --- | --- |
| `nus.driver_positions_local` | The real table. It stores rows on this node and copies them to its replica. |
| `nus.driver_positions` | A `Distributed` table. It stores nothing. It sends a query to both shards and puts the answers back together. |

**Always read and write the plain name.** The `_local` tables exist because
ClickHouse needs somewhere to actually put the data, and because a
materialized view has to read from a local table.

Each Distributed table has a **sharding key** that decides where a row goes:
positions by `driver_id` or `rider_id`, trip events by `trip_id`, hotspots
by `zone_id`. Rows about the same thing land on the same shard, so a query
about one driver reads half the cluster instead of all of it.

## What is stored

| Table | Holds | Kept for |
| --- | --- | --- |
| `driver_positions` | every driver position report | 90 days |
| `rider_positions` | every rider position report | 90 days |
| `trip_events` | every trip status change, enriched by the sink | 365 days |
| `hotspot_history` | the demand score of each zone over time | 365 days |
| `trip_stats_hourly` | completed trips summed per hour and pickup zone | kept |

Old data is removed automatically by the `TTL` rule on each table, so the
disk cannot grow forever.

`trip_events` carries columns Kafka does not: the demand score of the pickup
zone at that moment, whether the trip counts as a hotspot trip, and how far
the real duration drifted from the predicted one. `clickhouse-sink` fills
those in from Redis before inserting, which is what keeps those lookups off
the OLTP database.

### Reading the hourly summary correctly

`trip_stats_hourly` is filled by a materialized view, which runs **per
node**. The same hour and zone can therefore appear on both shards. Always
aggregate:

```sql
SELECT hour,
       sum(completed_trips)            AS trips,
       sum(revenue)                    AS revenue,
       sum(surge_sum) / sum(completed_trips) AS avg_surge
FROM nus.trip_stats_hourly
WHERE hour >= now() - INTERVAL 24 HOUR
GROUP BY hour
ORDER BY hour;
```

Reading a single row and treating it as the total would silently show half
the truth.

## Memory

`max_server_memory_usage` is 6.5 GB against an 8 GB container limit, and one
query may use at most 4 GB. ClickHouse otherwise sizes itself against the
whole machine, and in a container with no swap that ends in the container
being killed instead of a query being refused. A refused query says
"memory limit exceeded" and can be fixed; an OOM kill cannot.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | Three Keeper nodes, four data nodes, and the `ch-ddl-init` one-shot behind the `init` profile. |
| `config/keeper/keeper.xml` | Keeper settings, shared by all three nodes; only the id comes from the environment. |
| `config/clickhouse/config.d/cluster.xml` | The cluster shape, where Keeper is, and the `{shard}` / `{replica}` values. |
| `config/clickhouse/config.d/memory.xml` | Server memory ceiling and cache sizes. |
| `config/clickhouse/users.d/profiles.xml` | Per-query limits. The login itself comes from the environment. |
| `ddl/*.sql` | The tables, in name order. Every statement uses `IF NOT EXISTS`. |
| `ddl/apply-ddl.sh` | Applies those files. Safe to re-run. |
| `clickhouse-cluster-design.md` | Why the cluster has this shape. |
| `.env.example` | Template for the untracked `.env` (image pins, login). |

## Environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TZ` | `UTC` | Container timezone — the whole stack runs UTC. |
| `CH_SERVER_IMAGE` | `clickhouse/clickhouse-server:25.3` | Data node image, also used by the DDL one-shot. |
| `CH_KEEPER_IMAGE` | `clickhouse/clickhouse-keeper:25.3` | Keeper image. |
| `CH_USER` | `nus` | The login every client uses. |
| `CH_PASSWORD` | — (required) | Its password. Created by the image on first start. |

## Standalone quickstart

> Standalone runs use the `e-infra-clickhouse` project scope. The `nus-*`
> volumes are shared with the full stack, so mixing scopes prints a harmless
> `volume ... was created for project ...` warning. For stack assembly, run
> everything through the root compose per the [runbook](../README.md).

```bash
docker network create nus-backbone      # once, shared by the whole stack
cp .env.example .env                    # change CH_PASSWORD
docker compose up -d

# once the four nodes are healthy, create the tables
docker compose run --rm ch-ddl-init
```

## Verify

```bash
# all four nodes, as the cluster itself sees them
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT host_name, shard_num, replica_num FROM system.clusters WHERE cluster='nus_cluster'"

# Keeper answers, and one of the three is the leader
docker compose exec ch-keeper-1 bash -c \
  'exec 3<>/dev/tcp/localhost/9181; echo mntr >&3; cat <&3' | grep -E 'zk_server_state|zk_followers'

# the tables exist on this node
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT name, engine FROM system.tables WHERE database='nus' ORDER BY name"

# replication is keeping up: absolute_delay should stay at or near 0
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT table, is_leader, absolute_delay, queue_size FROM system.replicas"

# write on one node, read it back through the other shard's replica
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "INSERT INTO nus.hotspot_history VALUES ('smoke','night',0.5,1,1,1.0,now64(3))"
docker compose exec ch-s2r2 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT count() FROM nus.hotspot_history WHERE zone_id='smoke'"
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "ALTER TABLE nus.hotspot_history_local DELETE WHERE zone_id='smoke'"
```

A `queue_size` that keeps growing, or an `absolute_delay` in the hundreds of
seconds, means a replica is falling behind — usually a node that was down
and is still catching up.

## Failure demo

```bash
# stop one replica of shard 1: queries still answer, using the other copy
docker stop ch-s1r2
docker compose exec ch-s1r1 clickhouse-client --user nus --password "$CH_PASSWORD" \
  --query "SELECT count() FROM nus.trip_events"

# bring it back: it replays what it missed from its replica
docker start ch-s1r2
```

Losing **both** replicas of one shard is different: that half of the data is
unreachable until a node returns. That is a deliberate limit of a four-node
cluster, not a bug.

## Adding a table later

Add a numbered file to `ddl/` and run the one-shot again:

```bash
docker compose run --rm ch-ddl-init
```

Existing tables are untouched. Changing a live table is intentionally not
automated — that is a decision to make by hand, with the data in front of
you.

## Teardown

```bash
# keep the data
docker compose down

# destroy the data and the Keeper state as well. On the next start, run
# ch-ddl-init again to recreate the tables.
docker compose down -v
```
