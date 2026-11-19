# h-bootstrap — prepares the stack, once

One-shot: schema, LION graph restore, zones, people, a seeded history,
ClickHouse load, then Redis `system:bootstrap:done`. Services wait on that
marker. Nothing here writes to Kafka.

Image pins, seed sizes, and memory are in the root `.env`. Laptop seed is
small (`SEED_DRIVERS=1060`, one day). UTC.

History is routed through pgRouting (`HISTORY_ROUTING_WORKERS`). Position
reports are thinned (`HISTORY_POSITIONS_PER_TRIP`). The generator is
seeded, so the same settings produce the same week.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-bootstrap` and `graph-restore`. |
| `Dockerfile` | Python image. Graph restore uses the pgRouting image. |
| `migrations/*.sql` | Schema, in name order. |
| `bootstrap/` | The ten steps. |
| `graph-restore.sh` | Restores `routable-graph.dump`. |
| `lion-prepare/` | Builds that dump once, on the host. |
| `.env.example` | Standalone copy of the settings this piece reads. |

## Verify

From `meanul-hailpath/`:

```bash
make bootstrap
make verify-data
```

Marker `system:bootstrap:done` in Redis. Counts in Postgres and
`hailpath.trip_events`.

## Running it again

Idempotent while the marker is set. `FORCE_RESEED=true` runs the steps
again; existing rows are still kept (`ON CONFLICT DO NOTHING`). A full
wipe is `make destroy`.

## Teardown

```bash
docker compose --profile init down      # this one-shot is already exited
```
