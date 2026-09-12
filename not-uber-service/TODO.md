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

## Not yet done: people.seed()'s Faker calls are the next real bottleneck

Confirmed live: with the road-point-pooling fix in place, bootstrap's
people-seeding step still pegs one full CPU core (100% in `docker stats`)
for several minutes with zero database activity in between - `pg_stat_
activity` showed nothing from bootstrap, no locks, no new log lines,
while the container was genuinely working the whole time, not hung.
This is `people.seed()` (h-bootstrap/bootstrap/people.py) building
~106,000 driver + ~1,500,000 passenger records in memory before a single
INSERT runs - roughly 3.3 million Faker calls total (`faker.name()`,
`faker.msisdn()`, `faker.license_plate()`), all in one Python thread.

Threading will not help here the way it did for the database round
trips: Faker generation is pure CPU work, not I/O, so the GIL serializes
it across threads regardless of how many are started. Real speedup
needs genuine multiprocessing (e.g. `concurrent.futures.
ProcessPoolExecutor`, splitting the driver/passenger ranges across
BOOTSTRAP_CPUS-many worker processes, each with its own Faker instance
seeded deterministically from its own range) - the same class of fix as
history.py's own ThreadPoolExecutor for routing calls, except processes
instead of threads because this bottleneck is CPU-bound, not I/O-bound.

Keep Faker itself: the realism it buys (real-looking names/phone numbers/
plates, not "user_000123") is the whole reason people.py uses it over a
cheaper synthetic generator - the fix is parallelizing the existing
calls, not replacing them.
