#!/usr/bin/env bash
# Install the ClickHouse plugin into a host directory that Grafana mounts.
# Official Grafana, no derived image. Idempotent.
set -eu -o pipefail

env_file="${1:-.env}"

val() {
  grep -E "^${1}=" "${env_file}" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

root="$(val HAILPATH_VOLUME_ROOT)"
image="$(val GRAFANA_IMAGE)"
image="${image:-grafana/grafana:12.4.15}"
ver="$(val GRAFANA_CLICKHOUSE_PLUGIN_VERSION)"
ver="${ver:-4.23.5}"
# Every other download in this repository runs on the host: lion-fetch,
# tlc-*-fetch, tiles/prepare.sh and converter-prepare.sh all use host curl,
# and the image builds already default to BUILD_NETWORK=host. This one
# downloads from inside a container, so on the bridge it gets whatever
# resolver the daemon hands it - 8.8.8.8 when the host's resolv.conf names
# a loopback stub. Dionysus refuses outbound DNS to 8.8.8.8, so the plugin
# lookup failed there while the host's own network was fine. Same setting,
# same reason as the builds.
net="$(val BUILD_NETWORK)"
net="${net:-host}"

if [ -z "$root" ]; then
  printf '  FAIL    HAILPATH_VOLUME_ROOT is not set in %s\n' "${env_file}" >&2
  exit 1
fi

dir="${root}/hailpath-grafana-plugins"
plugin="${dir}/grafana-clickhouse-datasource"
stamp="${dir}/VERSION"
busybox="$(val BUSYBOX_IMAGE)"
busybox="${busybox:-busybox:1.38.0}"
mkdir -p "$dir"

have="$(cat "$stamp" 2>/dev/null || true)"
installed=""
if [ -f "${plugin}/plugin.json" ]; then
  installed="$(python3 -c "import json; print(json.load(open('${plugin}/plugin.json')).get('info',{}).get('version',''))" 2>/dev/null || true)"
fi
if [ -d "$plugin" ] && { [ "$have" = "$ver" ] || [ "$installed" = "$ver" ]; }; then
  docker run --rm --user 0:0 --memory 64m --memory-swap 64m \
    -v "${dir}:/plugins" "$busybox" \
    sh -c "printf '%s\n' '$ver' > /plugins/VERSION && chown -R 472:0 /plugins"
  printf '  ok      %s (%s)\n' "$plugin" "$ver"
  exit 0
fi
if [ -d "$plugin" ]; then
  printf '  old     %s (have %s, want %s) — replacing plugin\n' "$plugin" "${have:-${installed:-unknown}}" "$ver"
  # Grafana writes the plugin as uid 472. Host rm fails; busybox as root
  # on the same bind mount does not.
  docker run --rm --user 0:0 --memory 64m --memory-swap 64m \
    -v "${dir}:/plugins" "$busybox" \
    rm -rf /plugins/grafana-clickhouse-datasource
fi

printf '  from    grafana cli grafana-clickhouse-datasource %s\n' "$ver"
# A ceiling even on a plugin download: every docker run in this repo gets
# one, so no step can reach the host's OOM-killer on a small machine.
docker run --rm --user 0:0 --network "$net" --entrypoint grafana \
  --memory 512m --memory-swap 512m \
  -v "${dir}:/var/lib/grafana/plugins" \
  "$image" \
  cli --pluginsDir /var/lib/grafana/plugins plugins install grafana-clickhouse-datasource "$ver"

if [ ! -d "$plugin" ]; then
  printf '  FAIL    plugin directory was not created at %s\n' "$plugin" >&2
  exit 1
fi

docker run --rm --user 0:0 --memory 64m --memory-swap 64m \
  -v "${dir}:/plugins" "$busybox" \
  sh -c "printf '%s\n' '$ver' > /plugins/VERSION && chown -R 472:0 /plugins"
printf '  ok      %s (%s)\n' "$plugin" "$ver"
