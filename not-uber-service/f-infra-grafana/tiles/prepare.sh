#!/usr/bin/env bash
# Build nyc.mbtiles for tileserver-gl. Run from not-uber-service/ (make tiles-prepare).
#
# Host curl for every input (same as lion-fetch). Planetiler --download inside
# Docker failed on Dionysus: UnresolvedAddressException for
# dev.maptiler.download. Extras come from the OpenMapTiles *upstream* URLs
# (osmdata / naciscdn / acalcutt), not MapTiler's CDN.
#
# Idempotent: existing files are kept; curl -C - resumes a .part. make destroy
# keeps nus-tiles-data; make nuke is the only target that removes it.
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

root="$(val NUS_VOLUME_ROOT)"
if [ -z "${root}" ]; then
  printf '  FAIL    NUS_VOLUME_ROOT is not set in %s\n' "${env_file}" >&2
  exit 1
fi

dir="${root}/nus-tiles-data"
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

pbf_url="$(val GEOFABRIK_NY_PBF_URL)"
pbf_url="${pbf_url:-https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf}"
jar_url="$(val PLANETILER_JAR_URL)"
jar_url="${jar_url:-https://github.com/onthegomap/planetiler/releases/download/v0.8.4/planetiler.jar}"
jre="$(val JAVA_JRE_IMAGE)"
jre="${jre:-eclipse-temurin:21-jre-jammy}"
water_url="$(val WATER_POLYGONS_URL)"
water_url="${water_url:-https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip}"
ne_url="$(val NATURAL_EARTH_URL)"
ne_url="${ne_url:-https://naciscdn.org/naturalearth/packages/natural_earth_vector.sqlite.zip}"
# Planetiler documents this as the lake-centerlines upstream. The MapTiler
# mirror (dev.maptiler.download) did not resolve inside the JRE container;
# openmaptiles/osm-lakelines/raw/v0.9 is a 404.
lakes_url="$(val LAKE_CENTERLINES_URL)"
lakes_url="${lakes_url:-https://github.com/acalcutt/osm-lakelines/releases/download/latest/lake_centerline.shp.zip}"

fetch "${pbf_url}" "${dir}/new-york-latest.osm.pbf"
fetch "${jar_url}" "${dir}/planetiler.jar"
fetch "${water_url}" "${src}/water-polygons-split-3857.zip"
fetch "${ne_url}" "${src}/natural_earth_vector.sqlite.zip"
fetch "${lakes_url}" "${src}/lake_centerline.shp.zip"

printf '  running  Planetiler in %s (container has no network)\n' "${jre}"
docker run --rm --user 0:0 --network none \
  -e JAVA_TOOL_OPTIONS="-Xmx16g" \
  -w /data \
  -v "${dir}:/data" \
  "${jre}" \
  java -jar /data/planetiler.jar \
    --osm-path=/data/new-york-latest.osm.pbf \
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
