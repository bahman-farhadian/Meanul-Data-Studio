# z-lib/nus-common — shared building blocks

The seven Python components (`h-bootstrap` and the six services) all need
the same things: read settings from the environment, log in a way a machine
can read, connect to PostgreSQL through the proxy, find Redis through
Sentinel, produce and consume Avro on Kafka, insert into ClickHouse, and
shut down cleanly when Docker asks them to.

Writing that seven times would mean seven slightly different versions of it,
and seven places to fix the same bug. It lives here once instead.

`z-lib/` sorts last on purpose, next to `z-config/`: it is stack-level
support, not a step in the build order.

## What is inside

| Module | What it does |
| --- | --- |
| `config.py` | Reads settings from the environment, with clear errors for anything missing. |
| `logging.py` | One JSON line per log record, so logs can be searched instead of read. |
| `lifecycle.py` | Waits for the bootstrap marker, and turns Docker's stop signal into a clean shutdown. |
| `postgres.py` | Connections through `lb-a`/`lb-b`: writes on 5432, reads on 5433. |
| `redis_client.py` | Finds the current Redis primary through Sentinel, and builds the key names the stack agreed on. |
| `kafka.py` | Avro producer and consumer, with schemas loaded from the files in `c-infra-kafka/schemas/`. |
| `clickhouse.py` | Batched inserts through the entry tier. |
| `geo.py` | Small geography helpers: distance between two points, and which part of the day a moment belongs to. |

## How a component uses it

The build context of every Python component is `not-uber-service/`, so the
image can hold both the component and this package in the same shape as the
repository:

```
/build/z-lib/nus-common/     <- this package
/build/j-service-driver/     <- the component, and the working directory
```

The component's `pyproject.toml` then points at it by path:

```toml
dependencies = ["nus-common"]

[tool.uv.sources]
nus-common = { path = "../z-lib/nus-common" }
```

Because the layout inside the image matches the layout in the repository,
the same path works in both places.

## Rules this package follows

- **Read from Redis, not from PostgreSQL.** `postgres.py` exists for the
  components that own data (bootstrap, the generators, dispatch, city).
  Everything else looks things up in Redis. This is the rule from section 1
  of the main README, and it is why `redis_client.py` carries the key names.
- **Every timestamp is UTC.** Helpers return timezone-aware UTC values. The
  only place a local timezone appears is the pacing config, which is
  interpreted in `SIM_TIMEZONE`.
- **Nothing is retried forever in silence.** Waiting helpers log every
  attempt and give up with a clear message rather than hanging.
