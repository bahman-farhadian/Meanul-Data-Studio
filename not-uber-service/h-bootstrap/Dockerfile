# h-bootstrap — the one-shot that prepares the stack.
#
# Built in two stages. The first installs the Python dependencies; the second
# is the image that actually runs, and carries only what is needed at run
# time. That keeps uv and the build tools out of the final image.
#
# IMPORTANT: the build context is not-uber-service/, not this directory,
# because the image needs the shared package in z-lib/ as well. The layout
# inside the image mirrors the repository, so the path dependency in
# pyproject.toml resolves the same way in both places.
#
#   docker build -f h-bootstrap/Dockerfile .        <- from not-uber-service/

ARG PYTHON_IMAGE=python:3.13-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.17

# uv is copied out of its own published image rather than installed with pip:
# one binary, one pinned version, no dependency resolution to do first.
FROM ${UV_IMAGE} AS uv

# --------------------------------------------------------------------------
# Build stage: create the virtual environment
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app/h-bootstrap

# The shared package first: it changes less often than the component, so
# Docker can reuse this layer on most rebuilds.
COPY z-lib/nus-common /app/z-lib/nus-common
COPY h-bootstrap/pyproject.toml h-bootstrap/uv.lock /app/h-bootstrap/
COPY h-bootstrap/bootstrap /app/h-bootstrap/bootstrap

# --frozen: install exactly what uv.lock says, and fail if the lock file does
# not match pyproject.toml. A build must never quietly pick newer versions.
ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev

# --------------------------------------------------------------------------
# Runtime stage
# --------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

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

# Same paths as the build stage: the virtual environment records where it
# lives, so moving it would break it.
COPY --from=build /app /app
WORKDIR /app/h-bootstrap
ENV PATH="/app/h-bootstrap/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# Runs once and exits. A non-zero exit means the stack is not prepared, and
# the services keep waiting rather than working with half-made data.
CMD ["python", "-m", "bootstrap"]
