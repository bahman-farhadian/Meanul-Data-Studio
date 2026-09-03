# h-bootstrap — the one-shot that prepares the stack.
#
# Built in two stages. The first installs the Python dependencies; the second
# is the image that actually runs, and carries only what is needed at run
# time. That keeps uv and the build tooling out of the final image.
#
# IMPORTANT: the build context is not-uber-service/, not this directory,
# because the image needs the shared package in z-lib/ as well. The layout
# inside the image mirrors the repository, so the path dependency in
# pyproject.toml resolves the same way in both places.
#
#   docker build -f h-bootstrap/Dockerfile .        <- from not-uber-service/

ARG PYTHON_IMAGE=python:3.13.15-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.9

# uv is copied out of its own published image rather than installed with pip:
# one binary, one pinned version, and no dependency resolution to do first.
FROM ${UV_IMAGE} AS uv

# --------------------------------------------------------------------------
# Build stage: create the virtual environment
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app/h-bootstrap

# The shared package first: it changes less often than the component, so
# Docker reuses this layer on most rebuilds.
COPY z-lib/nus-common /app/z-lib/nus-common
COPY h-bootstrap/pyproject.toml h-bootstrap/uv.lock /app/h-bootstrap/

# --frozen: install exactly what uv.lock says. A build must never quietly
# pick up a newer version of anything.
#
# The cache mount is what makes a rebuild survive a network outage: uv keeps
# its downloads there, so changing application code re-runs this step against
# the cache instead of reaching for the index.
# hatchling is the one thing uv.lock cannot pin: it is a build-system
# requirement, resolved against the index on every build, so `--frozen` does
# not cover it. On a slow or intermittent link that single request is what
# stalls — uv's default is a 30 second timeout and three tries, which is not
# enough when one file takes two minutes. The cache mount below means it is
# fetched once and then read from disk.
ENV UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=180 \
    UV_HTTP_RETRIES=5 \
    UV_CONCURRENT_DOWNLOADS=4
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-project

# The application code comes AFTER the dependency install, so editing it
# cannot invalidate the layer above. With the source copied before it, a
# one-line change re-resolved every dependency — which fails outright on a
# host that has no route to the index at that moment.
COPY h-bootstrap/bootstrap /app/h-bootstrap/bootstrap
# The migrations are data the container reads at run time, not code, so it is
# easy to forget them here — and the failure comes much later, from inside a
# container that has already waited for five services to be healthy.
COPY h-bootstrap/migrations /app/h-bootstrap/migrations
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev

# --------------------------------------------------------------------------
# Runtime stage
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

ARG PYTHON_IMAGE
LABEL org.opencontainers.image.title="nus/bootstrap" \
      org.opencontainers.image.description="One-shot initialiser: migrations, NYC street map, seed data and a week of history" \
      org.opencontainers.image.source="https://github.com/bahman-farhadian/Meanul-Data-Studio" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="${PYTHON_IMAGE}"

# The map tools:
#   osmium-tool     cuts the extract down to the city and converts it
#   osm2pgrouting   turns the map into the routable graph pgRouting uses
#   ca-certificates lets the download verify the site it is talking to
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        osmium-tool \
        osm2pgrouting \
        ca-certificates \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Same paths as the build stage: a virtual environment records where it
# lives, so moving it would break it.
COPY --from=build /app /app

# Run as a normal user, like the six services do. Nothing here needs root.
#
# /data/osm is created HERE, owned by that user, because it is a mount point
# for a named volume: Docker seeds an empty named volume from the image's
# directory at that path, ownership included. Without this the volume would
# be created root-owned and the very first thing bootstrap does — write the
# downloaded map — would fail with a permission error.
RUN useradd --create-home --no-log-init --shell /usr/sbin/nologin --uid 10001 nus \
 && mkdir -p /data/osm \
 && chown -R nus:nus /app /data

WORKDIR /app/h-bootstrap
ENV PATH="/app/h-bootstrap/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER nus

# Fail the build rather than ship an image that cannot do its job. Both of
# these have been wrong before: the migrations were missing entirely, and the
# routing config comes from an apt package whose layout can change.
RUN python -c "import bootstrap, nus_common" \
 && test "$(ls -1 /app/h-bootstrap/migrations/*.sql | wc -l)" -gt 0 \
 && test -f /usr/share/osm2pgrouting/mapconfig_for_cars.xml \
 && echo "bootstrap: $(ls -1 /app/h-bootstrap/migrations/*.sql | wc -l) migrations, routing config present"

# Runs once and exits. A non-zero exit means the stack is not prepared, and
# the services keep waiting rather than working with half-made data.
CMD ["python", "-m", "bootstrap"]
