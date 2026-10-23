#!/usr/bin/env bash
# Fallback source for water-polygons-split-3857.zip, for when
# osmdata.openstreetmap.de is unreachable.
#
# That host is the only place the current file exists - it has no mirror,
# and the plugin's own GitHub releases carry no assets. On 2026-10-23 it
# refused HTTPS and then stopped answering altogether, which blocks
# tiles-prepare and so the whole Grafana basemap.
#
# OpenMapTiles' import-data image downloads that same zip at build time and
# ships the extracted shapefile inside. This repackages it into the archive
# Planetiler expects. Same dataset, same ODbL licence, same osmdata origin,
# and the shapefile's own README travels inside the zip - but it is a
# SNAPSHOT, not today's file, so this prints its date. Coastlines are the
# one layer where a few months of drift is defensible; nothing else here
# should ever be sourced this way.
#
# Pinned by digest, not :latest, because this image is still published and
# its contents would otherwise change under a redeploy. The older
# openmaptiles/import-water carries the same data from 2020-01-09 and is
# archived; this one was 2026-03-12 when it was pinned.
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
image="${image:-openmaptiles/import-data@sha256:8b48c4f7d68aac973e92523ca49862be7cb39febe78206c26b60504206e3656d}"

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
import pathlib, sys, zipfile
# import-water put the shapefile straight in /import; its successor
# import-data groups each source in its own directory. Find the .shp
# rather than hard-coding either layout.
shp = next(iter(sorted(pathlib.Path('/import').rglob('water_polygons.shp'))), None)
if shp is None:
    sys.exit('no water_polygons.shp in this image')
src = shp.parent
out = pathlib.Path('/out/water-polygons-split-3857.zip')
tmp = out.with_suffix('.zip.part')
inner = 'water-polygons-split-3857'
with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for f in sorted(src.iterdir()):
        if f.is_file():
            z.write(f, f'{inner}/{f.name}')
tmp.rename(out)
readme = src / 'README.txt'
if readme.exists():
    for line in readme.read_text().splitlines():
        if 'Date of the data' in line:
            print('  vintage ' + line.strip())
"

if [ ! -s "${dest}" ]; then
  printf '  FAIL    nothing was written to %s\n' "${dest}" >&2
  exit 1
fi
printf '  ok      %s (%s)\n' "${dest}" "$(du -h "${dest}" | cut -f1)"
printf '  %s\n' "A snapshot, not the live file. make tiles-prepare will now skip the"
printf '  %s\n' "download and build tiles from it. Delete it and re-run tiles-prepare"
printf '  %s\n' "against osmdata.openstreetmap.de if the vintage above matters."
