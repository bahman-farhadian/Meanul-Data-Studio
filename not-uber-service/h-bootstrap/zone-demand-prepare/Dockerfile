# syntax=docker/dockerfile:1
# The one-shot that turns a real month of NYC TLC trip records into the
# two small calibration tables real demand generation reads.
#
# Built once, run once by `make zone-demand-prepare`, then gone. Pure
# tabular aggregation over Parquet — a pinned Python image plus a lockfile,
# not `pip install pandas pyarrow` against whatever PyPI has today, and not
# python:3.13-slim (a floating tag).
#
# Layer order: lockfile first, prepare.py after. Editing the script must
# not re-download pandas.

ARG PYTHON_IMAGE=python:3.13.15-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.9

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

COPY h-bootstrap/zone-demand-prepare/pyproject.toml \
     h-bootstrap/zone-demand-prepare/uv.lock \
     /app/

ENV UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=180 \
    UV_HTTP_RETRIES=5
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-project

COPY h-bootstrap/zone-demand-prepare/prepare.py /app/prepare.py

FROM ${PYTHON_IMAGE}

ARG PYTHON_IMAGE
LABEL org.opencontainers.image.title="nus/zone-demand-prepare" \
      org.opencontainers.image.description="Throwaway one-shot: TLC trip records to demand-calibration CSVs" \
      org.opencontainers.image.base.name="${PYTHON_IMAGE}"

COPY --from=build /app /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

ENTRYPOINT ["python", "/app/prepare.py"]
