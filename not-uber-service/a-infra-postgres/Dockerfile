# syntax=docker/dockerfile:1
# One PostgreSQL with PostGIS and pgRouting.
#
# PostGIS and pgRouting are what the street graph needs. A second
# database on this same host is not a second machine.

ARG PG_IMAGE=postgres:18.6
FROM ${PG_IMAGE}

ARG PG_IMAGE
ARG PG_MAJOR=18

LABEL org.opencontainers.image.title="nus/postgres" \
      org.opencontainers.image.description="PostgreSQL with PostGIS and pgRouting for not-uber-service" \
      org.opencontainers.image.source="https://github.com/bahman-farhadian/Meanul-Data-Studio" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="${PG_IMAGE}"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        "postgresql-${PG_MAJOR}-postgis-3" \
        "postgresql-${PG_MAJOR}-pgrouting" \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Fail the build rather than ship a database that cannot load the street graph.
RUN test -f "/usr/share/postgresql/${PG_MAJOR}/extension/postgis.control" \
 && test -f "/usr/share/postgresql/${PG_MAJOR}/extension/pgrouting.control" \
 && echo "postgis and pgrouting present for PostgreSQL ${PG_MAJOR}"

COPY pg_hba.conf /etc/postgresql/pg_hba.conf
RUN chown postgres:postgres /etc/postgresql/pg_hba.conf

EXPOSE 5432
