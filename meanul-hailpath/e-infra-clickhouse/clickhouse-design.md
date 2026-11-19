# ClickHouse on one host

One process: `hailpath-clickhouse`. Grafana and `clickhouse-sink` use
HTTP 8123 and native 9000 on that process.

A second data copy on this host dies with the host. It does not buy
availability. There is nothing to coordinate.

Query names are the tables (`hailpath.trip_events`,
`hailpath.driver_positions`). There is no `*_local` table. Raw streams
are `MergeTree`. Rollups are `SummingMergeTree`, `AggregatingMergeTree`,
or `ReplacingMergeTree(event_time)`. `ch-ddl-init` applies `ddl/` against
this one server.

Container and server memory come from `.env` (`CH_MEM`,
`CH_MAX_SERVER_MEMORY`, `CH_MAX_QUERY_MEMORY`) so the cgroup and the
server ceiling move together. Losing the process loses the warehouse
until the volume is back.
