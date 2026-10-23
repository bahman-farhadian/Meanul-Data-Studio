#!/usr/bin/env bash
# Fallback source for water-polygons-split-3857.zip, for when
# osmdata.openstreetmap.de is unreachable.
#
# That host is the only place the current file exists - it has no mirror,
# and the plugin's own GitHub releases carry no assets. On 2026-10-23 it
# refused HTTPS and then stopped answering altogether, which blocks
# tiles-prepare and so the whole Grafana basemap.
#
# OpenMapTiles' water-import image downloads the same zip at build time and
# ships the extracted shapefile inside. This repackages it into the archive
# Planetiler expects. Same dataset, same ODbL licence, same osmdata origin -
# but the copy in that image is OLD, and this prints its date so nobody
# mistakes it for the current file. Coastlines are the one layer where that
# is defensible; nothing else here should ever be sourced this way.
#
# Opt-in on purpose: tiles-prepare still fetches the current file and this
# never runs on its own.
set -eu -o pipefail

env_file="${1:-.env}"

val() {
  grep -E "^${1}=" "${env_file}" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

root="$(val HAILPATH_VOLUME_ROOT)"
image="$(val WATER_POLYGONS_IMAGE)"
image="${image:-openmaptiles/import-water:latest}"

if [ -z "${root}" ]; then
  printf '  FAIL    HAILPATH_VOLUME_ROOT is not set in %s\n' "${env_file}" >&2
  exit 1
fi

src="${root}/hailpath-tiles-data/sources"
dest="${src}/water-polygons-split-3857.zip"
mkdir -p "${src}"

if [ -s "${dest}" ]; then
  printf '  ok      already there: %s (%s)\n' "${dest}" "$(du -h "${dest}" | cut -f1)"
  exit 0
fi

printf '  from    %s\n' "${image}"
docker pull -q "${image}" >/dev/null

# --network none because everything needed is already in the image, and the
# zip is written straight into the mounted sources directory rather than
# copied out, so an 887 MB shapefile is never on disk twice.
docker run --rm --network none --entrypoint python3 \
  -v "${src}:/out" "${image}" -c "
import pathlib, zipfile
src = pathlib.Path('/import')
out = pathlib.Path('/out/water-polygons-split-3857.zip')
tmp = out.with_suffix('.zip.part')
inner = 'water-polygons-split-3857'
with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for f in sorted(src.iterdir()):
        if f.is_file():
            z.write(f, f'{inner}/{f.name}')
tmp.rename(out)
for line in (src / 'README.txt').read_text().splitlines():
    if 'Date of the data' in line:
        print('  vintage ' + line.strip())
"

if [ ! -s "${dest}" ]; then
  printf '  FAIL    nothing was written to %s\n' "${dest}" >&2
  exit 1
fi
printf '  ok      %s (%s)\n' "${dest}" "$(du -h "${dest}" | cut -f1)"
printf '  %s\n' "This is NOT the current file. make tiles-prepare will now skip the"
printf '  %s\n' "download and build tiles from it. Delete it and re-run tiles-prepare"
printf '  %s\n' "once osmdata.openstreetmap.de is back, if the vintage matters."
