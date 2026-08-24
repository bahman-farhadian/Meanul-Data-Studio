# n-service-clickhouse-sink — Writes every event into ClickHouse, enriched from Redis
#
# Two stages. The first installs the dependencies; the second is what runs,
# and carries neither uv nor a compiler.
#
# IMPORTANT: the build context is not-uber-service/, not this directory. The
# image needs the shared package in z-lib/ and the Avro schemas in
# c-infra-kafka/, and it keeps the same layout as the repository so the path
# dependency in pyproject.toml resolves the same way in both places.
#
#   docker build -f n-service-clickhouse-sink/Dockerfile .     <- from not-uber-service/

ARG PYTHON_IMAGE=python:3.13-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.17

FROM ${UV_IMAGE} AS uv

# --------------------------------------------------------------------------
# Build stage
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app/n-service-clickhouse-sink

# The shared package first: it changes less often than the service, so this
# layer survives most rebuilds.
COPY z-lib/nus-common /app/z-lib/nus-common
COPY n-service-clickhouse-sink/pyproject.toml n-service-clickhouse-sink/uv.lock /app/n-service-clickhouse-sink/
COPY n-service-clickhouse-sink/clickhouse_sink /app/n-service-clickhouse-sink/clickhouse_sink

# --frozen: install exactly what uv.lock says. A build must never quietly
# pick up a newer version of anything.
ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev

# --------------------------------------------------------------------------
# Runtime stage
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

# Same paths as the build stage: a virtual environment records where it
# lives, so moving it would break it.
COPY --from=build /app /app

# The Avro schemas, taken from the component that owns them, so there is one
# copy of the truth in the repository and not one per service.
COPY c-infra-kafka/schemas /app/schemas

WORKDIR /app/n-service-clickhouse-sink
ENV PATH="/app/n-service-clickhouse-sink/.venv/bin:${PATH}" \
    SCHEMA_DIR=/app/schemas \
    PYTHONUNBUFFERED=1

# Run as a normal user. Nothing here needs root, and a container that cannot
# become root is one less thing to worry about.
RUN useradd --system --uid 10001 --create-home nus && chown -R nus:nus /app
USER nus

CMD ["python", "-m", "clickhouse_sink"]
