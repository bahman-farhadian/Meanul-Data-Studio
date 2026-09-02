# not-uber-service — stack runbook

How to bring the full stack up on a fresh host. **This file covers assembly
only** — what each component is and how to verify it lives in that
component's own README. Architecture: the repository's main
[README](../README.md).

Everything runs from this directory, through the [Makefile](Makefile):

```bash
make            # the help, which is the short version of this file
```

## The short version

```bash
make init          # create .env and the shared nus-backbone network
$EDITOR .env       # change every password in section 1
make pull          # fetch every pinned image (tags do move)
make up            # preflight, then the whole ordered bring-up
make etcd-existing # the one post-bootstrap step (local state — never commit)
make verify        # prove each layer is actually working
```

`make up` takes a while, and most of it is one step: `h-bootstrap` downloads
the New York street map and builds the routing graph out of it. Everything
else is minutes.

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
warehouse and the downloaded street map on whichever disk you choose,
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
if it were a fresh volume, and a half-initialised PostgreSQL or etcd is far
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
- the etcd cluster state matches whether the data volumes already exist

## What `make up` does, in order

The order is not cosmetic. Several steps only work once something else has
happened, which is the whole reason this is a Makefile and not one
`docker compose up`:

| Step | Command | Why here |
| --- | --- | --- |
| 1 | `make certgen` | etcd needs its TLS material before it starts. |
| 2 | `make kafka-dirs` | A new volume belongs to root and the broker is not root, so the volumes are handed over **before** the first start. |
| 3 | `up` pieces a–g | Infrastructure, waited on until every healthcheck passes. |
| 4 | `make topics` | Auto-creation is off, so topics are made on purpose — after the brokers answer. |
| 5 | `make ch-ddl` | **Before bootstrap**, which writes the seeded week into `nus.trip_events`. |
| 6 | `make superset-init` | Superset's own tables, admin user and ClickHouse connection. |
| 7 | `make bootstrap` | Migrations, the street map, the people, a week of history, then the `system:bootstrap:done` marker. |
| 8 | `make cdc-register` | The connector names the tables it follows, so they must exist first. |
| 9 | `up` pieces i–n | The six services, which were waiting on the marker. |

Each of those is also a target of its own, so a failed run is resumed by
fixing the cause and running the step again — every one of them is
idempotent. `make bootstrap` in particular can be re-run as often as needed:
existing rows are kept, an imported map is not imported twice, and the
warehouse is not loaded twice.

If `bootstrap` fails, the six services stay in standby **on purpose** rather
than generating trips for drivers that do not exist. That is the design, not
a hang.

## The one post-bootstrap step

```bash
make etcd-existing
```

This flips `ETCD_INITIAL_CLUSTER_STATE` from `new` to `existing` in
[a-infra-postgres/etcd.env](a-infra-postgres/etcd.env) and recreates the
three etcd containers; the data volumes persist.

**Never commit that flip.** A fresh clone has to bootstrap from empty
volumes, so the repository keeps `new`. On a running cluster, `new` would let
a member that lost its volume bootstrap its own one-node cluster — split
brain. Reasoning:
[a-infra-postgres/README.md](a-infra-postgres/README.md#etcd-cluster-lifecycle--new-vs-existing).

## Verifying it

```bash
make verify        # every layer, in order
```

or one layer at a time — `verify-pg`, `verify-redis`, `verify-kafka`,
`verify-cdc`, `verify-ch`, `verify-dash`, `verify-data`. Each prints what a
healthy answer looks like underneath the output, and each component's own
README explains the checks in full.

Two results that look wrong and are not:

- **Red rows on the HAProxy stats page are expected.** The checks ask Patroni
  which *role* a node holds, so `pg_write` shows 1 UP (the leader) and
  `pg_read` shows 2 UP (the replicas). A node down in **both** backends is
  the only real failure signal.
- **Empty dashboard panels before bootstrap finishes are fine.** An error is
  not.

## Running it

```bash
make urls          # where to point a browser or a client
make ps            # what is running
make health        # health, restart counts, OOM kills, one line each
make stats         # live memory and CPU against each container's limit
make oom           # anything killed for memory, or restart-looping
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
make psql          # the current leader          make redis-cli
make psql-read     # the replica pool            make ch-client
make patronictl ARGS=list
```

Failover demos:

```bash
make failover-pg      # hand the PostgreSQL leadership to another node
make failover-redis   # ask Sentinel to promote a replica
```

## Bringing one piece up at a time

The pieces can still be brought up individually, in the alphabetic build
order, which is how each was written and tested:

```bash
make up-a   # PostgreSQL (Patroni) + etcd, behind the entry tier
make up-b   # Redis + Sentinel
make up-c   # Kafka + Schema Registry        (then: make topics)
make up-d   # Debezium Connect               (register the connector after piece h)
make up-e   # ClickHouse + Keeper            (then: make ch-ddl)
make up-f   # Grafana
make up-g   # Superset                       (then: make superset-init)
make bootstrap
make cdc-register
make up-services
```

Each waits for its healthchecks before returning, so a piece that does not
come up stops the sequence where the problem is.

## Adding a component later

Write `<letter>-<kind>-<name>/docker-compose.yaml` and its README, add the
`include:` entry to the root [`docker-compose.yaml`](docker-compose.yaml),
add its settings to [`.env.example`](.env.example) in a section of their own,
add its `listen` block to
[z-config/haproxy/haproxy.cfg](z-config/haproxy/haproxy.cfg) plus the
`LB_A_*`/`LB_B_*` port lines if it is proxied, add it to the right group
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
passwords are not lost by surprise. It also puts `ETCD_INITIAL_CLUSTER_STATE`
back to `new`, so the next `make up` can bootstrap from empty volumes.

Afterwards the host is as it was, with one exception it will not touch for
you: Docker's shared build cache, which is not this project's alone. Clear
that yourself with `docker builder prune` if you want the disk back.

Both destroy the downloaded street map, so the next bootstrap downloads it
again.
