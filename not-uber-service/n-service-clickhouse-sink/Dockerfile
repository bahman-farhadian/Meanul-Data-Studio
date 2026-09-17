# syntax=docker/dockerfile:1
# n-service-clickhouse-sink — Consumes every Kafka topic, enriches from Redis and batch-inserts into ClickHouse
#
# Two stages. The first installs third-party wheels from uv.lock; the second
# is what runs, and carries neither uv nor a compiler.
#
# Layer order is the cache contract: lockfiles first, application source
# after. Editing a .py file must not re-resolve PyPI.
#
# IMPORTANT: the build context is not-uber-service/, not this directory. The
# image needs the shared package in z-lib/ and the Avro schemas in
# c-infra-kafka/, and it keeps the same layout as the repository so the path
# dependency in pyproject.toml resolves the same way in both places.
#
#   docker build -f n-service-clickhouse-sink/Dockerfile .     <- from not-uber-service/

ARG PYTHON_IMAGE=python:3.13.15-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.9

# uv is copied out of its own published image rather than installed with pip:
# one binary, one pinned version, and no dependency resolution to do first.
FROM ${UV_IMAGE} AS uv

# --------------------------------------------------------------------------
# Build stage
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app/n-service-clickhouse-sink

# Metadata only. nus-common's Python source is copied after the wheel
# install, so a change to routing.py does not invalidate this layer.
COPY z-lib/nus-common/pyproject.toml /app/z-lib/nus-common/pyproject.toml
COPY n-service-clickhouse-sink/pyproject.toml n-service-clickhouse-sink/uv.lock /app/n-service-clickhouse-sink/

# --frozen: install exactly what uv.lock says. A build must never quietly
# pick up a newer version of anything.
#
# --no-install-project / --no-install-local skip this service and the path
# dependency until their source is in the image. hatchling is pinned in
# [build-system], so this layer does not float to whatever PyPI has today.
ENV UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=180 \
    UV_HTTP_RETRIES=5 \
    UV_CONCURRENT_DOWNLOADS=4
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-project --no-install-local

COPY z-lib/nus-common /app/z-lib/nus-common
COPY n-service-clickhouse-sink/clickhouse_sink /app/n-service-clickhouse-sink/clickhouse_sink
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev

# --------------------------------------------------------------------------
# Runtime stage
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

ARG PYTHON_IMAGE
LABEL org.opencontainers.image.title="nus/clickhouse-sink" \
      org.opencontainers.image.description="Consumes every Kafka topic, enriches from Redis and batch-inserts into ClickHouse" \
      org.opencontainers.image.source="https://github.com/bahman-farhadian/Meanul-Data-Studio" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="${PYTHON_IMAGE}"

# tzdata: ZoneInfo("America/New_York") needs the IANA db; python:*-slim
# does not ship /usr/share/zoneinfo. useradd before COPY so a source
# change does not re-run apt-get.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --no-log-init --shell /usr/sbin/nologin --uid 10001 nus

COPY --from=build --chown=nus:nus /app /app
COPY --chown=nus:nus c-infra-kafka/schemas /app/schemas

WORKDIR /app/n-service-clickhouse-sink
ENV PATH="/app/n-service-clickhouse-sink/.venv/bin:${PATH}" \
    SCHEMA_DIR=/app/schemas \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER nus

# Fail the build rather than ship an image whose entrypoint cannot import
# itself — a broken path or a missed COPY would otherwise only show up as a
# crash loop after deployment.
RUN python -c "import clickhouse_sink, nus_common; print('nus/clickhouse-sink imports cleanly')"

CMD ["python", "-m", "clickhouse_sink"]
