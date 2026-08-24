# Superset with the ClickHouse driver installed.
#
# The base image ships drivers for the common databases but not for
# ClickHouse, so clickhouse-connect is added here. It is installed at build
# time, not at start time, so the container needs no internet to run.

ARG SUPERSET_IMAGE=apache/superset:4.1.1
FROM ${SUPERSET_IMAGE}

ARG CLICKHOUSE_CONNECT_VERSION=0.8.15

# The image runs as an unprivileged user; installing packages needs root.
USER root
RUN pip install --no-cache-dir "clickhouse-connect==${CLICKHOUSE_CONNECT_VERSION}"
USER superset
