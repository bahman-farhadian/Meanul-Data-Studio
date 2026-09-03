# Superset with the ClickHouse driver installed.
#
# The base image ships drivers for the common databases but not for
# ClickHouse, so clickhouse-connect is added here — at build time, not at
# start time, so the container needs no internet to run.
#
# THE ONE THING TO KNOW ABOUT THIS FILE
#
# Superset runs out of a virtualenv at /app/.venv, but `pip` on PATH belongs
# to the system interpreter OUTSIDE that virtualenv. So the obvious
#
#     RUN pip install clickhouse-connect
#
# succeeds, reports success, and installs into
# /usr/local/lib/python3.10/site-packages — which Superset never looks at.
# The image builds, the container starts, and the ClickHouse connection then
# fails at runtime with "no module named clickhouse_connect", a long way from
# the cause.
#
# The virtualenv is built by uv and has no pip of its own, so the install has
# to be aimed at it explicitly. The verification line below is not decoration:
# it imports the driver with the same interpreter Superset uses, so this class
# of mistake fails the build instead of shipping.

ARG SUPERSET_IMAGE=apache/superset:6.1.0
FROM ${SUPERSET_IMAGE}

ARG SUPERSET_IMAGE
ARG CLICKHOUSE_CONNECT_VERSION=1.7.2
# Superset ships no PostgreSQL driver of its own — verified by importing
# psycopg2 in the stock image, where it is absent. Without it the read-only
# PostgreSQL connection registered by init/register_database.py cannot open,
# and the failure appears only when somebody runs a query in SQL Lab.
ARG PSYCOPG2_VERSION=2.9.12

LABEL org.opencontainers.image.title="nus/superset" \
      org.opencontainers.image.description="Apache Superset with the ClickHouse driver, for the not-uber-service stack" \
      org.opencontainers.image.source="https://github.com/bahman-farhadian/Meanul-Data-Studio" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="${SUPERSET_IMAGE}"

# Installing packages needs root; the image runs as an unprivileged user.
USER root

RUN uv pip install --python /app/.venv/bin/python --no-cache \
        "clickhouse-connect==${CLICKHOUSE_CONNECT_VERSION}" \
        "psycopg2-binary==${PSYCOPG2_VERSION}" \
 && python -c "import clickhouse_connect, clickhouse_connect.cc_sqlalchemy, psycopg2; \
import sqlalchemy; \
sqlalchemy.create_engine('clickhousedb://u:p@h:8123/d'); \
sqlalchemy.create_engine('postgresql+psycopg2://u:p@h:5433/d'); \
print('drivers importable by Superset: clickhouse-connect', clickhouse_connect.__version__, '/ psycopg2', psycopg2.__version__.split()[0])"

USER superset
