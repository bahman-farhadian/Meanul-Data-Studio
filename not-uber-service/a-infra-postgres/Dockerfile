# Patroni-managed PostgreSQL node for the not-uber-service OLTP cluster.
# Base image is pinned via PG_IMAGE (default postgres:18.4); PostGIS and
# pgRouting are baked in so h-bootstrap's migrations can CREATE EXTENSION.

ARG PG_IMAGE=postgres:18.4
FROM ${PG_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        python3 \
        python3-venv \
        postgresql-18-postgis-3 \
        postgresql-18-pgrouting \
    && python3 -m venv /opt/patroni \
    && /opt/patroni/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/patroni/bin/pip install --no-cache-dir \
        "patroni[etcd3]~=4.0" \
        "psycopg[binary]~=3.2" \
    && ln -s /opt/patroni/bin/patroni /usr/local/bin/patroni \
    && ln -s /opt/patroni/bin/patronictl /usr/local/bin/patronictl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY patroni.yml /etc/patroni/patroni.yml
RUN chown -R postgres:postgres /etc/patroni

USER postgres
EXPOSE 5432 8008

CMD ["patroni", "/etc/patroni/patroni.yml"]
