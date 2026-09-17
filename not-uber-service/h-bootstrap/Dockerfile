# syntax=docker/dockerfile:1
# h-bootstrap — the one-shot that prepares the stack.
#
# Built in two stages. The first installs third-party wheels from uv.lock;
# the second is the image that actually runs, and carries only what is
# needed at run time. That keeps uv and the build tooling out of the final
# image.
#
# Layer order is the cache contract: lockfiles first, application source
# after. Editing a .py file must not re-resolve PyPI.
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

# Metadata only. nus-common's Python source is copied after the wheel
# install, so a change to routing.py does not invalidate this layer.
COPY z-lib/nus-common/pyproject.toml /app/z-lib/nus-common/pyproject.toml
COPY h-bootstrap/pyproject.toml h-bootstrap/uv.lock /app/h-bootstrap/

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

# postgresql-client: pg_restore, to load the routable graph h-bootstrap/
# lion-prepare/ already built on the host - this container never builds the
# graph itself, only restores it.
#
# useradd and the apt packages live in this layer, before the application
# copy, so a Python change does not re-run apt-get.
#
# /data/lion is created HERE, owned by that user, because it is a mount
# point for a named volume: Docker seeds an empty named volume from the
# image's directory at that path, ownership included. Without this the
# volume would be created root-owned and reading the precomputed graph
# dump would fail with a permission error.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        postgresql-client \
        ca-certificates \
        tzdata \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --no-log-init --shell /usr/sbin/nologin --uid 10001 nus \
 && mkdir -p /data/lion \
 && chown -R nus:nus /data

COPY --from=build --chown=nus:nus /app /app

WORKDIR /app/h-bootstrap
ENV PATH="/app/h-bootstrap/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER nus

# Fail the build rather than ship an image that cannot do its job. The
# migrations have been missing entirely before.
RUN python -c "import bootstrap, nus_common" \
 && test "$(ls -1 /app/h-bootstrap/migrations/*.sql | wc -l)" -gt 0 \
 && echo "bootstrap: $(ls -1 /app/h-bootstrap/migrations/*.sql | wc -l) migrations present"

# Runs once and exits. A non-zero exit means the stack is not prepared, and
# the services keep waiting rather than working with half-made data.
CMD ["python", "-m", "bootstrap"]
