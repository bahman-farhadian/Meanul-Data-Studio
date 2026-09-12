# Known issues to come back to

## Not yet done: replica scaling for cache-updater and clickhouse-sink

Both services already share `KAFKA_GROUP_ID` across instances rather than
minting a per-instance one, and neither has a fixed `container_name`
any more (removed specifically for this) - so `docker compose up --scale
cache-updater=N` / `--scale clickhouse-sink=N` should already work,
Kafka's own consumer-group partition assignment splitting cdc.*/
driver_location/etc. across the replicas with no code change. Not yet
wired into `make up` or verified live: needs a real run to confirm
rebalancing behaves, and `make ps`/`make logs`/`make errors` need a look
to make sure they still make sense against N containers under one
service name instead of one.

## Not yet done: sharding driver-service/passenger-service for real multi-core use

Both are a single Python process running one synchronous loop with no
internal threading - confirmed directly, no `threading`/`asyncio`/
`multiprocessing` anywhere in either. A lone instance cannot use more
than about one core's worth of Python bytecode no matter how high its
own `cpus:` ceiling goes (the CPU bump this round gives the underlying
librdkafka/libpq C-level I/O room, not the Python loop itself more
parallelism). Unlike cache-updater/clickhouse-sink, these two are
Kafka *producers*, not consumer-group members, so they can't get the
same free ride from partition assignment - genuine multi-core use here
needs an actual code change (e.g. shard the driver/passenger id space
by hash across N replicas, each instance only handling its own slice).
Worth doing only after confirming the config-propagation fix and the
vertical CPU bump this round aren't already enough on their own.

Currently pending real data: deploying at the current commit specifically
to measure driver-service/passenger-service CPU utilization and actual
throughput against the theoretical target before deciding.
