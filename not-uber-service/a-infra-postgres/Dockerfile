# Patroni-managed PostgreSQL node for the not-uber-service OLTP cluster.
#
# Three things are added to the official image: PostGIS and pgRouting, so
# h-bootstrap's migrations can CREATE EXTENSION and the street graph has
# somewhere to live, and Patroni itself, which is what turns three of these
# containers into one cluster with an elected leader.
#
# Patroni is installed into its own virtualenv rather than into the system
# interpreter. The base image's Python belongs to the distribution, and pip
# writing into it is exactly how a later apt upgrade breaks a working node.

ARG PG_IMAGE=postgres:18.6
FROM ${PG_IMAGE}

ARG PG_IMAGE
ARG PG_MAJOR=18
ARG PATRONI_VERSION=4.0

LABEL org.opencontainers.image.title="nus/patroni-postgres" \
      org.opencontainers.image.description="PostgreSQL with PostGIS, pgRouting and Patroni, for the not-uber-service OLTP cluster" \
      org.opencontainers.image.source="https://github.com/bahman-farhadian/Meanul-Data-Studio" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="${PG_IMAGE}"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        curl \
        python3 \
        python3-venv \
        "postgresql-${PG_MAJOR}-postgis-3" \
        "postgresql-${PG_MAJOR}-pgrouting" \
 && python3 -m venv /opt/patroni \
 && /opt/patroni/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/patroni/bin/pip install --no-cache-dir \
        "patroni[etcd3]~=${PATRONI_VERSION}" \
        "psycopg[binary]~=3.2" \
 && ln -s /opt/patroni/bin/patroni /usr/local/bin/patroni \
 && ln -s /opt/patroni/bin/patronictl /usr/local/bin/patronictl \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Fail the build rather than ship a node that cannot do its job. Each of
# these has gone wrong before in ways that only surface much later: a
# PostGIS package built for the wrong major version leaves no control file,
# and a Patroni that cannot import its etcd3 extra fails at leader election
# rather than at start-up.
RUN test -f "/usr/share/postgresql/${PG_MAJOR}/extension/postgis.control" \
 && test -f "/usr/share/postgresql/${PG_MAJOR}/extension/pgrouting.control" \
 && patroni --version \
 && /opt/patroni/bin/python -c "import patroni.dcs.etcd3" \
 && echo "postgis, pgrouting and patroni[etcd3] all present for PostgreSQL ${PG_MAJOR}"

COPY patroni.yml /etc/patroni/patroni.yml
RUN chown -R postgres:postgres /etc/patroni

USER postgres
EXPOSE 5432 8008

CMD ["patroni", "/etc/patroni/patroni.yml"]
