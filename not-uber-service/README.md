# not-uber-service — stack runbook

How to bring the full stack up on a fresh host. **This file covers assembly
only** — what each component is and how to verify it lives in that
component's own README. Architecture: the repository's main
[README](../README.md). Studio assessment (schema, broker, generation,
simulation; version-1 bindings): [ASSESSMENT.md](../ASSESSMENT.md).

Everything runs from this directory, through the [Makefile](Makefile):

```bash
make            # the help, which is the short version of this file
```

## Every command, in order

From a clone to a running, verified stack. Run them in this order — several
steps only work once an earlier one has happened.

```bash
# --- once, to set up ------------------------------------------------------
git clone <this repo> && cd Meanul-Data-Studio/not-uber-service
make init                  # .env, the nus-backbone network, the data directories
$EDITOR .env               # section 1: the eight passwords. Nothing else is required.
make prepare               # pull every image, build the eleven, prepare LION + OSM tiles
                           # THE ONLY STEP THAT REACHES OUTSIDE THIS HOST

# --- the deployment (needs no internet) -----------------------------------
make up                    # preflight, then the whole ordered bring-up:
                           #   volume-perms -> lb-config -> ksqldb-secrets
                           #   -> infrastructure (a-g, including grafana+tiles)
                           #   -> topics -> schemas -> ksql-ddl -> ch-ddl
                           #   -> superset-init -> bootstrap -> cdc-register
                           #   -> services (i-o)
make verify                # prove each layer works (includes ksqlDB and /tiles/)

# --- running it -----------------------------------------------------------
make urls                  # where to point a browser or a client
make ps                    # what is running
make lag                   # consumer lag — the pipeline's health signal
make errors                # exit codes, OOM kills, healthcheck output, error logs
make stats                 # live usage against each limit

# --- stopping and removing ------------------------------------------------
make stop                  # stop the containers, keep everything
make down                  # remove the containers, keep the data
make destroy               # remove the data too, but KEEP LION and OSM tiles
make clean                 # leave no trace: images, network, .env, graph and all
```

Two of those deserve a note.

**`make lion-fetch` and `make lion-prepare`** build the routable street graph
on the host, entirely outside the stack — `lion-fetch` downloads NYC's
official LION street data (DCP), `lion-prepare` filters it to real drivable
streets, computes real costs from its DOT-verified posted speed limits and
one-way directions, and builds the routing topology, all in a throwaway
container that never touches the deployed stack. Both are part of
`make prepare` and skip themselves once already done — `lion-prepare` is the
slow one on a first run (filtering and topology-building the whole dataset);
every run after that restores its cached result in seconds. `make preflight`
tells you when either is missing.

**`make destroy` keeps the prepared graph and OSM tiles** on purpose — they
are slow to rebuild, and NYC's streets do not change between two test runs.
`make clean` and `make nuke` remove them along with everything else.

**`make up` does not rebuild images.** After a code change, without
re-downloading:

```bash
make build && make destroy && make up
```

Skip `make prepare` unless `routable-graph.dump` or `nyc.mbtiles` is missing.
Piece f is Grafana **and** the tileserver; omitting tiles leaves Grafana maps
at HTTP 503 (`No server is available to handle this request`).

Every step of `make up` is also a target of its own, and every one is
idempotent, so a failed run is resumed by fixing the cause and running that
step again rather than starting over.

## Deploying without outside network

`make prepare` is the only step that reaches the internet: it pulls the
pinned images, builds the eleven this repository defines, and downloads and
prepares the LION routable graph and the OSM MBTiles for Grafana. Everything
after it is local — the containers talk to each other on `nus-backbone`, and
those downloads are already on disk, ready to restore.

That separation matters wherever network access is restricted, intermittent,
or only available from the host itself. Run `make prepare` where the host can
reach the internet; run `make up` anywhere, including with no network at all.

Builds already run in the host's own network namespace by default
(`BUILD_NETWORK=host`) — a build stage's traffic is forwarded through the
bridge, which is exactly what a route in front of this host often cannot
see, even while the host's own connections work fine. It changes nothing
about how the stack runs. If a build ever fails while `docker pull` succeeds
anyway, set `BUILD_NETWORK=default` and re-run — some hosts are the other
way around.

`make up` never builds — that is `make prepare`'s job — because a build
re-resolves image metadata from the registry even when the image already
exists locally, which is a network call. `make preflight` checks every image
is present and says plainly when nothing left to do needs the internet.

## One settings file

Copy `.env.example` to `.env` and edit that one file. The root compose file
includes all fourteen components **without** an `env_file:`, so every one of
them resolves its `${...}` from this directory's `.env`. There is one place a
value can come from, and no set of files to keep in step by hand.

Only **section 1 must be edited** — the passwords. Everything below it works
as shipped.

Two consequences worth knowing:

- PostgreSQL's password reaches its three consumers under three different
  names (`PG_SUPERUSER_PASSWORD` creates the account, `PG_PASSWORD` is how
  the services log in, `CDC_PG_PASSWORD` is how Debezium does). In `.env`
  the last two are written as `${PG_SUPERUSER_PASSWORD}`, so setting the
  password once sets all three. This used to be three files that had to
  agree.
- The six services' Kafka consumer groups are six separate settings
  (`DRIVER_GROUP_ID`, `SINK_GROUP_ID`, …) and must stay distinct. Two
  services sharing a group do not each get a copy of the stream — Kafka
  splits the partitions between them, so each silently receives a fraction
  of what it expects.

The per-component `.env.example` files are still the reference for what each
component consumes, and are what a **standalone** single-component run uses
(`docker compose up` inside that directory). They take no part in the full
stack.

## Where the data lives

The 26 named volumes are bind-mounted to a directory tree under
`NUS_VOLUME_ROOT`, set in `.env`. That puts the databases, the topics, the
warehouse and the prepared street graph on whichever disk you choose,
**without touching the Docker daemon's configuration**.

Images are the exception, and there is no way around it: images, container
writable layers and the build cache all live under the daemon's `data-root`,
and no compose setting can move them. So the split is deliberate — images
stay wherever `data-root` points, and the data volumes go on the fast, large
disk, which is where the growth and the I/O actually are.

`make init` creates the tree; `make preflight` refuses to deploy if the root
is missing, unwritable, or short of space.

**The one thing to know:** because these are bind mounts, `docker volume rm`
and `docker compose down -v` remove the volume *entry* and leave every byte
on disk. `make destroy` and `make clean` therefore delete the tree
explicitly — and verify it went, rather than assuming. If files remain
(containers write as their own users, so root may be needed) they say so and
exit non-zero, because a leftover tree is picked up by the next bring-up as
if it were a fresh volume, and a half-initialised PostgreSQL is far
worse than none.

## Before the first deployment

`make preflight` runs on its own before every `make up`, and refuses to
continue if anything below is wrong. Run it early — it costs nothing and it
answers "will this host actually take the stack" before any image is pulled:

- Docker is reachable and the compose plugin is v2+ (v1 cannot do `include:`)
- the host has the cores and memory the budget assumes
- **there is room for the images on Docker's data-root** (~15 GB), and room
  for the data under `NUS_VOLUME_ROOT`, where ClickHouse grows 1–2 GB a day
- `.env` exists, holds no `change-me` placeholders, defines every required
  setting, and defines none of them twice
- all fourteen components resolve from it
- every host port the entry tier publishes is free

## What `make up` does, in order

The order is not cosmetic. Several steps only work once something else has
happened, which is the whole reason this is a Makefile and not one
`docker compose up`:

| Step | Command | Why here |
| --- | --- | --- |
| 1 | `make volume-perms` | The volumes are bind mounts and take the host directory's ownership, so each is handed to the user that writes to it **before** anything starts. |
| 2 | `make lb-config` | Renders `haproxy.cfg` with `REDIS_PASSWORD` baked in — HAProxy does not expand `${VAR}` from its own environment inside a health check, so this has to happen **before** `lb-a` starts. |
| 3 | `make ksqldb-secrets` | Writes the ksqlDB basic-auth file before `ksqldb-server` starts. |
| 4 | start Debezium Connect | Started but **not** waited for: it spends minutes scanning its plugins, and nothing needs it until `cdc-register`. |
| 5 | `up` pieces a–g | One Postgres, one Redis, one Kafka broker with Schema Registry and ksqlDB, one ClickHouse, Grafana **and** `nus-tiles`, Superset. Waited on until every healthcheck passes. Debezium is not in that wait. |
| 6 | `make topics`, `make schemas`, `make ksql-ddl` | Auto-creation is off. Topics are replication factor 1. Schemas register with Schema Registry. ksqlDB is the SQL reader of that broker. |
| 7 | `make ch-ddl` | **Before bootstrap**, which writes the seeded week into `nus.trip_events`. |
| 8 | `make superset-init` | Superset's own tables, admin user and ClickHouse connection. |
| 9 | `make bootstrap` | Migrations, the street graph (restored, already prepared by `make prepare`), the people, history, then the `system:bootstrap:done` marker. |
| 10 | `make cdc-register` | The connector names the tables it follows, so they must exist first — and Connect has had the whole bootstrap to become ready. It tails `nus-pg-1` through the write port. |
| 11 | `up` pieces i–o | The services, which were waiting on the marker. |

Each of those is also a target of its own, so a failed run is resumed by
fixing the cause and running the step again — every one of them is
idempotent. `make bootstrap` in particular can be re-run as often as needed:
existing rows are kept, an imported map is not imported twice, and the
warehouse is not loaded twice.

If `bootstrap` fails, the six services stay in standby **on purpose** rather
than generating trips for drivers that do not exist. That is the design, not
a hang.

## Verifying it

```bash
make verify        # every layer, in order
```

or one layer at a time — `verify-pg`, `verify-redis`, `verify-kafka`,
`verify-ksqldb`, `verify-cdc`, `verify-ch`, `verify-dash`, `verify-data`.
Each prints what a healthy answer looks like underneath the output, and
each component's own README explains the checks in full.

`verify-pg` is `pg_isready`. `verify-redis` is a PING. `verify-kafka`
expects one replica in sync. `verify-ch` lists MergeTree engines.

Two results that look wrong and are not:

- **A down server on an HAProxy backend means that one process failed the
  check.** Ports 5432 and 5433 are both `nus-pg-1`. Ports 6379 and 6380
  are both `nus-redis-1`.
- **Empty dashboard panels before bootstrap finishes are fine.** An error is
  not.

## Running it

```bash
make urls          # where to point a browser or a client
make ps            # what is running
make health        # health, restart counts, OOM kills, one line each
make stats         # live memory and CPU against each container's limit
make errors        # exit codes, OOM kills, healthcheck output, error logs
make lag           # consumer lag per group — the pipeline's health signal
make logs SVC=dispatch-service
```

Then let it run for a few hours and watch those stay flat, as described in
the main README, section 2.9. The three numbers that matter are consumer lag,
OOM kills, and memory against the limits.

**If anything falls behind, turn the volume down in `.env` — never by
removing containers.** `DRIVER_TICK_SECONDS` and `TRIP_REQUESTS_PER_MINUTE`
are the two dials that matter; dispatch is the slowest step in the pipeline
because it runs one pgRouting query per trip, so fewer requests is the fix.

A shell into any of the data stores, through the proxy where there is one:

```bash
make psql          # port 5432, nus-pg-1         make redis-cli
make psql-read     # port 5433, same process     make ch-client
```

## Bringing one piece up at a time

The pieces can still be brought up individually, in the alphabetic build
order, which is how each was written and tested:

```bash
make up-piece PIECE=a   # one PostgreSQL, behind the entry tier
make up-piece PIECE=b   # one Redis
make up-piece PIECE=c   # one Kafka broker, Schema Registry, ksqlDB
make up-piece PIECE=d   # Debezium Connect               (register the connector after piece h)
make up-piece PIECE=e   # one ClickHouse                 (then: make ch-ddl)
make up-piece PIECE=f   # Grafana
make up-piece PIECE=g   # Superset                       (then: make superset-init)
make bootstrap
make cdc-register
make up-piece PIECE=services
```

Each waits for its healthchecks before returning, so a piece that does not
come up stops the sequence where the problem is.

### Worked example: piece `a` alone, from a fresh clone

`make up-piece` only starts containers — it never builds or pulls, and it
never runs the one-shots a piece needs before its first start. Bringing up
one piece in isolation is those three things done by hand, scoped to that
piece, followed by `up-piece`:

```bash
# once, regardless of which piece: the master .env, the shared network,
# and the whole data-directory tree (all 27 directories — the merged
# compose model is interpolated as one file, so this isn't piece-specific)
make init
$EDITOR .env        # Section 1 — all 8 passwords need real values, even
                     # though piece a only reads three of them: every
                     # ${VAR:?...} across all 14 components is checked
                     # before Docker will start anything at all

# build/pull ONLY piece a's images — not `make prepare`, which does all 14
docker compose build pg-1
docker compose pull lb-a

# the one-shots piece a needs before its first start
docker compose run --rm volume-perms          # whole tree, harmless to run unscoped
docker compose run --rm haproxy-config-render # lb-a's config — piece a's entry tier needs this too

# start it — lb-a, pg-1, nothing else
make up-piece PIECE=a

make ps
make errors
make verify-pg
```

Connect a SQL client through `lb-a`
(see [Connecting](a-infra-postgres/README.md#connecting)):
host = this server, port `5432` or `5433` (both are `nus-pg-1`), database
`nus`, user `postgres`, password = `PG_SUPERUSER_PASSWORD`. The `nus`
schema and its tables arrive with `h-bootstrap`, several pieces later.

Skip `make preflight` and `make prepare` for this: both check readiness of
all 14 components and will fail on the 13 you have not touched yet. They
become the right tools again once every piece is ready and you are doing
the full-stack pass.

The build/pull lines are specific to piece `a` — each later piece gets its
own two lines here as we reach it, rather than a guessed-at general form for
components not yet verified.

## Adding a component later

Write `<letter>-<kind>-<name>/docker-compose.yaml` and its README, add the
`include:` entry to the root [`docker-compose.yaml`](docker-compose.yaml),
add its settings to [`.env.example`](.env.example) in a section of their own,
add its `listen` block to
[z-config/haproxy/haproxy.cfg](z-config/haproxy/haproxy.cfg) plus the
`LB_A_*` port lines if it is proxied, add it to the right group
variable in the [Makefile](Makefile), and update the tables in the main
[README](../README.md).

## Nothing runs on the host

Every part of this project runs in a container. There is no virtualenv, no
`pip install`, no Python, no Node and no database client to install on the
host — a Python component's dependencies are installed from its `uv.lock`
inside its own image, and the shells in `make psql` / `make redis-cli` /
`make ch-client` are the clients already inside the containers.

What the host actually needs is Docker with the compose plugin, GNU make,
and the coreutils any Linux already has. `make preflight` reports on the
host; it never changes it.

The only things the project puts on the host are Docker's own objects —
containers, images and one network — the data tree under `NUS_VOLUME_ROOT`,
and your `.env`. All of it is removable with a single command, below.

## Teardown

```bash
make stop          # stop the containers, keep everything
make down          # remove the containers, KEEP the data volumes
make destroy       # remove the containers and DESTROY every data volume
make clean-images  # remove every image it built, pulled, or built FROM
make clean         # LEAVE NO TRACE: all of the above, plus the network and .env
```

`make destroy` and `make clean` both ask for confirmation.

`make clean` is the one to run when you are finished with the stack: it
removes every container, the whole data tree under `NUS_VOLUME_ROOT`, every
image (including the base images the custom ones were built from), the
`nus-backbone` network, and moves your `.env` aside to `.env.removed` so the
passwords are not lost by surprise. The next `make up` starts from empty
volumes.

Afterwards the host is as it was, with one exception it will not touch for
you: Docker's shared build cache, which is not this project's alone. Clear
that yourself with `docker builder prune` if you want the disk back.

Both destroy the prepared street graph, so the next `make prepare` downloads
and builds it again.
