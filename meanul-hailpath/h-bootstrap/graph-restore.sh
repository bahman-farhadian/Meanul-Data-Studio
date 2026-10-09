#!/bin/bash
# Restore the pre-built LION graph using the same pgRouting image as the
# database. Bootstrap's Python image does not carry a PostgreSQL 18 client.
set -euo pipefail

if [ "${SKIP_MAP_IMPORT:-false}" = "true" ]; then
  echo "SKIP_MAP_IMPORT is set — not restoring the street graph"
  exit 0
fi

dump=/data/lion/routable-graph.dump
if [ ! -s "$dump" ]; then
  echo "missing or empty: $dump" >&2
  echo "Run make lion-fetch && make lion-prepare (also part of make prepare)." >&2
  exit 1
fi

echo "waiting for PostgreSQL at ${PGHOST:-hailpath-proxy}:${PGPORT:-5432} ..."
attempt=1
while [ "$attempt" -le 30 ]; do
  if psql -d postgres -c "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  echo "  not ready yet (attempt ${attempt}/30)"
  attempt=$((attempt + 1))
  sleep 2
done
if [ "$attempt" -gt 30 ]; then
  echo "PostgreSQL never answered" >&2
  exit 1
fi

# Extensions live in public; migrations install them before this one-shot.
if ! psql -d "${PGDATABASE}" -tAc "SELECT 1 FROM pg_extension WHERE extname = 'pgrouting'" | grep -q 1; then
  echo "pgrouting is not installed in ${PGDATABASE} yet — run the schema phase first" >&2
  exit 1
fi

if psql -d "${PGDATABASE}" -tAc "SELECT to_regclass('public.ways')" | grep -q ways; then
  n=$(psql -d "${PGDATABASE}" -tAc "SELECT count(*) FROM ways")
  if [ "${n:-0}" -gt 0 ]; then
    echo "street graph already present ($n ways)"
    exit 0
  fi
fi

echo "restoring $dump into ${PGDATABASE}"
pg_restore --no-owner --no-privileges --dbname="${PGDATABASE}" "$dump"
echo "street graph restored"
