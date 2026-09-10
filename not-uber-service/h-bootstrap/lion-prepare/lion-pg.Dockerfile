# The throwaway database `make lion-prepare` builds the routable graph in.
#
# postgis/postgis bundles PostGIS but not pgRouting (confirmed directly,
# not assumed: `CREATE EXTENSION pgrouting` fails on the vanilla image with
# "extension is not available"). This just adds the one matching package.

ARG POSTGIS_IMAGE=postgis/postgis:17-3.5

FROM ${POSTGIS_IMAGE}

# The postgis/postgis image is Debian-based and already has the pinned
# PostgreSQL 17 apt repository configured - postgresql-17-pgrouting is the
# exact matching package, confirmed against a running container of this
# same base image.
#
# Check-Valid-Until=false: the base image's own bundled Debian release has
# an expired (not broken, just old) security repo signature window. This
# still verifies every package's actual signature - it only skips the
# separate "is this metadata too old" timestamp check, standard practice for
# an older base image's apt repo, and fine for a throwaway build-time-only
# dependency like this one.
RUN apt-get update -o Acquire::Check-Valid-Until=false \
 && apt-get install -y --no-install-recommends postgresql-17-pgrouting \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*
