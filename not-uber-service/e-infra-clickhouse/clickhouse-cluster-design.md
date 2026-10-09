# ClickHouse on one host

One ClickHouse process, `ch-s1r1` / `nus-ch-s1r1`. Grafana, Superset,
and `clickhouse-sink` query it through HAProxy on 8123 (HTTP) and 9000
(native). Both ports are that process. `lb-a` is the stable address.

The numbers match the root [README](../../README.md) section 2.9.

## Why one process

| Layer | Containers | CPU | Memory |
| --- | --- | --- | --- |
| ClickHouse | 1 (`ch-s1r1`) | 1.3 | 8 GB |
| Entry tier | 1 (`lb-a`) | 0.5 | 128 MB |

A second data copy on this host is not a second machine. It dies when
the host dies, and it spends CPU and disk on a copy that does not
survive the failure you would be buying it for. With one process there is nothing
for it to coordinate.

The service name stays `ch-s1r1`. `make ch-client`, Compose
`depends_on`, and the HAProxy server line already use it.

## Tables

The names services and dashboards query are the tables. `nus.trip_events`
and `nus.driver_positions` store the rows. There is no `*_local` table.

Raw streams use `MergeTree`. Rollups use `SummingMergeTree`,
`AggregatingMergeTree`, or `ReplacingMergeTree(event_time)`.
DDL is plain `CREATE TABLE` in `ddl/`. `ch-ddl-init` applies it once
against `nus-ch-s1r1`.

`max_server_memory_usage` is 6.5 GB inside the 8 GB container limit, so
a heavy query fails with a memory error instead of the cgroup killing
the process.

## Loss

There is one copy. Losing the process loses the warehouse until the
volume is back and the server starts. HAProxy keeps the host ports
stable. It does not hide a second server.
