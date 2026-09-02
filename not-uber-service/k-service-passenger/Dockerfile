# k-service-passenger — Simulates the riders
#
# Two stages. The first installs the dependencies; the second is what runs,
# and carries neither uv nor a compiler.
#
# IMPORTANT: the build context is not-uber-service/, not this directory. The
# image needs the shared package in z-lib/ and the Avro schemas in
# c-infra-kafka/, and it keeps the same layout as the repository so the path
# dependency in pyproject.toml resolves the same way in both places.
#
#   docker build -f k-service-passenger/Dockerfile .     <- from not-uber-service/

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

WORKDIR /app/k-service-passenger

# The shared package first: it changes less often than the service, so this
# layer survives most rebuilds.
COPY z-lib/nus-common /app/z-lib/nus-common
COPY k-service-passenger/pyproject.toml k-service-passenger/uv.lock /app/k-service-passenger/
COPY k-service-passenger/passenger_service /app/k-service-passenger/passenger_service

# --frozen: install exactly what uv.lock says. A build must never quietly
# pick up a newer version of anything.
ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev

# --------------------------------------------------------------------------
# Runtime stage
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

ARG PYTHON_IMAGE
LABEL org.opencontainers.image.title="nus/passenger-service" \
      org.opencontainers.image.description="Simulates the riders: trip requests and rider device location stream" \
      org.opencontainers.image.source="https://github.com/bahman-farhadian/Meanul-Data-Studio" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="${PYTHON_IMAGE}"

# Same paths as the build stage: a virtual environment records where it
# lives, so moving it would break it.
COPY --from=build /app /app

# The Avro schemas, taken from the component that owns them, so there is one
# copy of the truth in the repository and not one per service.
COPY c-infra-kafka/schemas /app/schemas

WORKDIR /app/k-service-passenger
ENV PATH="/app/k-service-passenger/.venv/bin:${PATH}" \
    SCHEMA_DIR=/app/schemas \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as a normal user. Nothing here needs root, and a container that cannot
# become root is one less thing to worry about.
RUN useradd --create-home --no-log-init --shell /usr/sbin/nologin --uid 10001 nus \
 && chown -R nus:nus /app
USER nus

# Fail the build rather than ship an image whose entrypoint cannot import
# itself — a broken path or a missed COPY would otherwise only show up as a
# crash loop after deployment.
RUN python -c "import passenger_service, nus_common; print('nus/passenger-service imports cleanly')"

CMD ["python", "-m", "passenger_service"]
