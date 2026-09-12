# Known issues to come back to

## Not yet done: Postgres's trips table has no retention/archival policy

Confirmed while sizing `make volume-quotas`: `trips` (route geometry
included) grows for as long as the stack runs live, with nothing ever
pruning or archiving it - unlike ClickHouse's own tables, which all
have a TTL. At real scale this is a real, if slower, version of the
same problem driver_location/driver_positions had: `pgdata-1/2/3`'s
XFS quota (25G each) is a safety net against it filling the disk, not
a fix - once hit, writes to `trips` start failing instead of the table
growing further. Needs a real decision (archive completed trips into
ClickHouse then prune Postgres? cap total retained trips? something
else?), not something to pick unilaterally.

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

## Not yet done: pool road-snapped points in the live services too

bootstrap's own per-driver/per-trip database round trip for
random_road_point_in_zone is fixed (build_road_point_pools /
pooled_road_point_in_zone, h-bootstrap/bootstrap/zones.py) - but
driver-service and passenger-service call the exact same underlying
nearest_road_point() directly, live, still paying a real query every
time: once per driver at startup (up to 106,000 sequential calls, no
threading), and twice per new trip request (~910/minute at full-scale
TRIP_REQUESTS_PER_MINUTE, forever, not just once). Same fix, different
location: the pooling logic needs to move from bootstrap/zones.py into
nus_common (routing.py or citygrid.py) so every service - bootstrap,
driver-service, passenger-service - can build its own pool once at its
own process startup instead of bootstrap's copy only helping bootstrap.
