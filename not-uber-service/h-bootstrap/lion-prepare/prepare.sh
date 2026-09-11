#!/bin/bash
# Turns the downloaded LION zip into a routable graph, once.
#
# Runs inside the throwaway lion-prepare container `make lion-prepare`
# brings up alongside a throwaway lion-pg. Everything this script produces
# that matters lives in one file afterwards: routable-graph.dump. The
# container, and the database it briefly held, are both thrown away right
# after.
set -euo pipefail

INPUT_ZIP="/data/lion.zip"
OUTPUT_DUMP="/data/routable-graph.dump"
WORKDIR="/tmp/lion"

export PGHOST="${LION_PG_HOST:-lion-pg}"
export PGPORT="${LION_PG_PORT:-5432}"
export PGUSER="${LION_PG_USER:-postgres}"
export PGPASSWORD="${LION_PG_PASSWORD:?set it}"
export PGDATABASE="${LION_PG_DATABASE:-lion}"

echo "waiting for lion-pg..."
for _ in $(seq 1 60); do
    pg_isready -q && break
    sleep 2
done
pg_isready -q || { echo "lion-pg never became ready"; exit 1; }

echo "unpacking $INPUT_ZIP"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
unzip -q -o "$INPUT_ZIP" -d "$WORKDIR"

GDB_DIR="$(find "$WORKDIR" -maxdepth 3 -iname "*.gdb" -type d | head -1)"
if [ -z "$GDB_DIR" ]; then
    echo "no .gdb directory found inside the LION download - its internal layout may have changed"
    exit 1
fi
echo "found geodatabase: $GDB_DIR"

echo "importing the lion layer (this is the slow step)"
ogr2ogr -f "PostgreSQL" \
    "PG:host=$PGHOST port=$PGPORT dbname=$PGDATABASE user=$PGUSER password=$PGPASSWORD" \
    "$GDB_DIR" lion \
    -t_srs EPSG:4326 \
    -lco GEOMETRY_NAME=geom \
    -nln lion_raw \
    -nlt CONVERT_TO_LINEAR \
    -overwrite \
    -progress

echo "filtering to drivable streets and building the routable graph"
psql -v ON_ERROR_STOP=1 -f /build-graph.sql

echo "counting the result"
psql -v ON_ERROR_STOP=1 -c "SELECT count(*) AS ways FROM ways;"
psql -v ON_ERROR_STOP=1 -c "SELECT count(*) AS vertices, count(*) FILTER (WHERE on_main_network) AS on_main_network FROM ways_vertices_pgr;"
psql -v ON_ERROR_STOP=1 -c "
    SELECT count(*) AS total_segments,
           count(*) FILTER (WHERE NULLIF(TRIM(posted_speed), '') IS NULL) AS default_speed_segments
      FROM lion_filtered;
"

echo "dumping ways and ways_vertices_pgr to $OUTPUT_DUMP"
pg_dump -Fc -t ways -t ways_vertices_pgr -f "$OUTPUT_DUMP"

echo "done: $OUTPUT_DUMP ($(du -h "$OUTPUT_DUMP" | cut -f1))"
