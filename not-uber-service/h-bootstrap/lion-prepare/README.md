# h-bootstrap/lion-prepare — builds the routable graph, once

A throwaway stack, entirely separate from `not-uber-service`'s own compose
project. `make lion-fetch` (downloads NYC's official LION street data) and
`make lion-prepare` (this directory) both run as part of `make prepare` — on
the host, before the real stack exists, never as part of a deployed
container.

## Why a separate stack

Filtering LION to real drivable streets, computing real costs from its
DOT-verified posted speed limits and one-way directions, and building the
routing topology depend on nothing this project generates — only on the
external LION release. That work is identical on every run, so it is done
once and cached (`NUS_VOLUME_ROOT/nus-lion-data/routable-graph.dump`) rather
than repeated on every `make destroy && make up` cycle. `h-bootstrap`'s own
`osm.py::import_map()` only ever restores that cached result.

## What runs

| Service | What it does |
| --- | --- |
| `lion-pg` | A throwaway PostGIS+pgRouting database (`postgis/postgis` image). Exists for the duration of this run only. |
| `lion-prepare` | Unzips the LION download, `ogr2ogr`s it into `lion-pg` with the CRS transform, runs `build-graph.sql`, then `pg_dump`s the finished `ways`/`ways_vertices_pgr` tables out to the host volume. |

`build-graph.sql` is the one file worth reading closely: it filters to real,
drivable streets (`FeatureTyp`/`SegmentTyp`/`TrafDir`), computes `cost_s`/
`reverse_cost_s` from real segment length and real posted speed limits, builds
the routing topology (`pgr_createTopology`), and marks which vertices are
actually reachable from each other (`on_main_network`) — the one thing every
later point-picking call (`nus_common.routing.nearest_road_point`) trusts, so
a generated trip can never reference a point that was never really on a road.

## Running it by hand

```
make lion-fetch      # downloads the current LION release, skipped if already there
make lion-prepare    # builds the graph, skipped if already built
```

Both are idempotent — safe to re-run, and normally never run directly since
`make prepare` already calls them in order.

To force a rebuild (a new LION edition, or a change to `build-graph.sql`),
delete the cached dump first:

```
rm -f "$NUS_VOLUME_ROOT/nus-lion-data/routable-graph.dump"
make lion-prepare
```
