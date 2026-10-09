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
image="${image:-grafana/grafana:12.4.10}"
ver="$(val GRAFANA_CLICKHOUSE_PLUGIN_VERSION)"
ver="${ver:-4.21.2}"

if [ -z "$root" ]; then
  printf '  FAIL    HAILPATH_VOLUME_ROOT is not set in %s\n' "${env_file}" >&2
  exit 1
fi

dir="${root}/hailpath-grafana-plugins"
plugin="${dir}/grafana-clickhouse-datasource"
mkdir -p "$dir"

if [ -d "$plugin" ]; then
  printf '  ok      already there: %s\n' "$plugin"
  exit 0
fi

printf '  from    grafana cli grafana-clickhouse-datasource %s\n' "$ver"
docker run --rm --user 0:0 --entrypoint grafana \
  -v "${dir}:/var/lib/grafana/plugins" \
  "$image" \
  cli --pluginsDir /var/lib/grafana/plugins plugins install grafana-clickhouse-datasource "$ver"

if [ ! -d "$plugin" ]; then
  printf '  FAIL    plugin directory was not created at %s\n' "$plugin" >&2
  exit 1
fi

chown -R 472:0 "$dir" 2>/dev/null || true
printf '  ok      %s\n' "$plugin"
