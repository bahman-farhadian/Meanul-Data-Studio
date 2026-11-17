#!/usr/bin/env bash
# Build nyc.mbtiles for tileserver-gl. Run from meanul-hailpath/ (make tiles-prepare).
#
# Host curl for every input (same as lion-fetch). Planetiler --download inside
# Docker failed on Dionysus: UnresolvedAddressException for
# dev.maptiler.download. Extras come from the OpenMapTiles *upstream* URLs
# (osmdata / naciscdn / acalcutt), not MapTiler's CDN.
#
# Idempotent: existing files are kept; curl -C - resumes a .part. make destroy
# keeps hailpath-tiles-data; make nuke is the only target that removes it.
set -eu -o pipefail

env_file="${1:-.env}"

val() {
  grep -E "^${1}=" "${env_file}" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

fetch() {
  local url="$1" dest="$2"
  if [ -s "${dest}" ]; then
    printf '  kept     %s (%s)\n' "${dest}" "$(du -sh "${dest}" | cut -f1)"
    return 0
  fi
  printf '  fetching %s\n' "${url}"
  curl -fL --retry 5 --retry-all-errors --connect-timeout 30 -C - \
    --progress-bar -o "${dest}.part" "${url}"
  mv "${dest}.part" "${dest}"
  printf '  wrote    %s (%s)\n' "${dest}" "$(du -sh "${dest}" | cut -f1)"
}

root="$(val HAILPATH_VOLUME_ROOT)"
if [ -z "${root}" ]; then
  printf '  FAIL    HAILPATH_VOLUME_ROOT is not set in %s\n' "${env_file}" >&2
  exit 1
fi

dir="${root}/hailpath-tiles-data"
src="${dir}/sources"
mbtiles="${dir}/nyc.mbtiles"
mkdir -p "${dir}" "${src}"
printf '  dir     %s\n' "${dir}"

if [ -f "${mbtiles}" ]; then
  size="$(wc -c < "${mbtiles}")"
  if [ "${size}" -gt 10485760 ]; then
    printf '  skipped %s already exists (%s)\n' "${mbtiles}" "$(du -sh "${mbtiles}" | cut -f1)"
    exit 0
  fi
  printf '  discarding incomplete %s (%s bytes)\n' "${mbtiles}" "${size}"
  rm -f "${mbtiles}"
fi

# Geofabrik publishes New York *State* - 498MB, of which the city is a
# small corner. BBBike cuts a New York *City* extract at 153MB, which is
# the whole area this project ever renders. The state file stays the
# default so the deployed map does not silently change; a dev machine
# overrides GEOFABRIK_NY_PBF_URL to the city extract and gets a usable
# basemap for a third of the download and a fraction of the Planetiler
# heap. The file name below follows the URL so both can coexist on disk.
pbf_url="$(val GEOFABRIK_NY_PBF_URL)"
pbf_url="${pbf_url:-https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf}"
jar_url="$(val PLANETILER_JAR_URL)"
jar_url="${jar_url:-https://github.com/onthegomap/planetiler/releases/download/v0.8.4/planetiler.jar}"
jre="$(val JAVA_JRE_IMAGE)"
jre="${jre:-eclipse-temurin:21-jre-noble}"
water_url="$(val WATER_POLYGONS_URL)"
water_url="${water_url:-https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip}"
ne_url="$(val NATURAL_EARTH_URL)"
ne_url="${ne_url:-https://naciscdn.org/naturalearth/packages/natural_earth_vector.sqlite.zip}"
# Planetiler documents this as the lake-centerlines upstream. The MapTiler
# mirror (dev.maptiler.download) did not resolve inside the JRE container;
# openmaptiles/osm-lakelines/raw/v0.9 is a 404.
lakes_url="$(val LAKE_CENTERLINES_URL)"
lakes_url="${lakes_url:-https://github.com/acalcutt/osm-lakelines/releases/download/latest/lake_centerline.shp.zip}"

pbf_file="${dir}/$(basename "${pbf_url}")"
fetch "${pbf_url}" "${pbf_file}"
fetch "${jar_url}" "${dir}/planetiler.jar"
fetch "${water_url}" "${src}/water-polygons-split-3857.zip"
fetch "${ne_url}" "${src}/natural_earth_vector.sqlite.zip"
fetch "${lakes_url}" "${src}/lake_centerline.shp.zip"

# The JVM heap and the container ceiling are two separate numbers and both
# have to be set. -Xmx alone tells the JVM what it may use; without
# --memory the container is unbounded, so a JVM that overshoots its heap -
# or any native allocation beside it - reaches the host's own OOM-killer,
# which picks a victim anywhere on the machine. On the 125GB deploy host
# that never showed. On a 31GB workstation with no swap it takes the
# machine down. Keep PLANETILER_MEM comfortably above PLANETILER_HEAP:
# the JVM needs headroom outside the heap for metaspace and buffers.
heap="$(val PLANETILER_HEAP)"; heap="${heap:-8g}"
plmem="$(val PLANETILER_MEM)"; plmem="${plmem:-10g}"
printf '  running  Planetiler in %s (container has no network, heap %s, ceiling %s)\n' \
  "${jre}" "${heap}" "${plmem}"
docker run --rm --user 0:0 --network none \
  --memory "${plmem}" --memory-swap "${plmem}" \
  -e JAVA_TOOL_OPTIONS="-Xmx${heap}" \
  -w /data \
  -v "${dir}:/data" \
  "${jre}" \
  java -jar /data/planetiler.jar \
    --osm-path="/data/$(basename "${pbf_file}")" \
    --water-polygons-path=/data/sources/water-polygons-split-3857.zip \
    --natural-earth-path=/data/sources/natural_earth_vector.sqlite.zip \
    --lake-centerlines-path=/data/sources/lake_centerline.shp.zip \
    --download=false \
    --download-osm-tile-weights=false \
    --use-wikidata=false \
    --force \
    --output=/data/nyc.mbtiles

if [ ! -s "${mbtiles}" ]; then
  printf '  FAIL    planetiler did not write %s\n' "${mbtiles}" >&2
  exit 1
fi
printf '  wrote    %s (%s)\n' "${mbtiles}" "$(du -sh "${mbtiles}" | cut -f1)"
