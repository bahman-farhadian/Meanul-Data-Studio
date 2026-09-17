# syntax=docker/dockerfile:1
# The one-shot that turns a raw LION download, and NYC TLC's real taxi
# zones, into a routable graph plus a real zone table.
#
# Built once, run once by `make lion-prepare`, then gone - this image never
# runs as part of the deployed stack. It exists only to hold gdal (ogr2ogr,
# to read LION's Esri File Geodatabase format and TLC's GeoJSON export) and
# a postgres client (pg_dump/psql) next to each other, which the deployed
# pieces have no other reason to carry.
#
# debian:13-slim is a rolling suite tag. The dated snapshot is the pin.

ARG DEBIAN_IMAGE=debian:trixie-20260824-slim

FROM ${DEBIAN_IMAGE}

ARG DEBIAN_IMAGE
LABEL org.opencontainers.image.title="nus/lion-prepare" \
      org.opencontainers.image.description="Throwaway gdal+psql image that builds the routable LION graph" \
      org.opencontainers.image.base.name="${DEBIAN_IMAGE}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        gdal-bin \
        postgresql-client \
        unzip \
        ca-certificates \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY h-bootstrap/lion-prepare/prepare.sh /prepare.sh
COPY h-bootstrap/lion-prepare/build-graph.sql /build-graph.sql
COPY h-bootstrap/lion-prepare/taxi-zones.sql /taxi-zones.sql
RUN chmod +x /prepare.sh

ENTRYPOINT ["/prepare.sh"]
