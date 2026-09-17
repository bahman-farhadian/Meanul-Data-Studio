#!/usr/bin/env bash
# Build nyc.mbtiles for tileserver-gl. Run from not-uber-service/ (make tiles-prepare).
#
# Optional .env keys are missing on hosts that have not re-run `make init`.
# Grep of a missing key must not fail the script: this Makefile uses
# `bash -eu -o pipefail`, so a bare grep -E '^UNSET_KEY=' is Error 1
# with no message (confirmed on Dionysus).
set -eu -o pipefail

env_file="${1:-.env}"

val() {
  grep -E "^${1}=" "${env_file}" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

root="$(val NUS_VOLUME_ROOT)"
if [ -z "${root}" ]; then
  printf '  FAIL    NUS_VOLUME_ROOT is not set in %s\n' "${env_file}" >&2
  exit 1
fi

dir="${root}/nus-tiles-data"
mkdir -p "${dir}"
printf '  dir     %s\n' "${dir}"

if [ -s "${dir}/nyc.mbtiles" ]; then
  printf '  skipped %s already exists (%s)\n' "${dir}/nyc.mbtiles" "$(du -sh "${dir}/nyc.mbtiles" | cut -f1)"
  exit 0
fi

pbf_url="$(val GEOFABRIK_NY_PBF_URL)"
pbf_url="${pbf_url:-https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf}"
jar_url="$(val PLANETILER_JAR_URL)"
jar_url="${jar_url:-https://github.com/onthegomap/planetiler/releases/download/v0.8.4/planetiler.jar}"
jre="$(val JAVA_JRE_IMAGE)"
jre="${jre:-eclipse-temurin:21-jre-jammy}"

pbf="${dir}/new-york-latest.osm.pbf"
jar="${dir}/planetiler.jar"

if [ ! -s "${pbf}" ]; then
  printf '  fetching %s\n' "${pbf_url}"
  curl -fL --retry 5 --progress-bar -o "${pbf}.part" "${pbf_url}"
  mv "${pbf}.part" "${pbf}"
  printf '  wrote    %s (%s)\n' "${pbf}" "$(du -sh "${pbf}" | cut -f1)"
else
  printf '  kept     %s (%s)\n' "${pbf}" "$(du -sh "${pbf}" | cut -f1)"
fi

if [ ! -s "${jar}" ]; then
  printf '  fetching %s\n' "${jar_url}"
  curl -fL --retry 5 --progress-bar -o "${jar}.part" "${jar_url}"
  mv "${jar}.part" "${jar}"
  printf '  wrote    %s (%s)\n' "${jar}" "$(du -sh "${jar}" | cut -f1)"
else
  printf '  kept     %s (%s)\n' "${jar}" "$(du -sh "${jar}" | cut -f1)"
fi

printf '  running  Planetiler in %s\n' "${jre}"
docker run --rm --user 0:0 \
  -e JAVA_TOOL_OPTIONS="-Xmx16g" \
  -v "${dir}:/data" \
  "${jre}" \
  java -jar /data/planetiler.jar \
    --osm-path=/data/new-york-latest.osm.pbf \
    --download \
    --output=/data/nyc.mbtiles

if [ ! -s "${dir}/nyc.mbtiles" ]; then
  printf '  FAIL    planetiler did not write %s/nyc.mbtiles\n' "${dir}" >&2
  exit 1
fi
printf '  wrote    %s (%s)\n' "${dir}/nyc.mbtiles" "$(du -sh "${dir}/nyc.mbtiles" | cut -f1)"
