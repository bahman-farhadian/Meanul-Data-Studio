#!/usr/bin/env bash
# Download Confluent's Avro converter jars into a host directory that the
# official Debezium Connect image mounts. Idempotent.
set -eu -o pipefail

env_file="${1:-.env}"

val() {
  grep -E "^${1}=" "${env_file}" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

root="$(val HAILPATH_VOLUME_ROOT)"
ver="$(val AVRO_CONVERTER_VERSION)"
ver="${ver:-8.3.3}"

if [ -z "$root" ]; then
  printf '  FAIL    HAILPATH_VOLUME_ROOT is not set in %s\n' "${env_file}" >&2
  exit 1
fi

dir="${root}/hailpath-connect-plugins/confluent-avro-converter"
mkdir -p "$dir"
stamp="${dir}/VERSION"

jars=$(find "$dir" -name '*.jar' 2>/dev/null | wc -l)
have="$(cat "$stamp" 2>/dev/null || true)"
if [ "$jars" -gt 0 ] && [ "$have" = "$ver" ]; then
  printf '  ok      %s (%s jars, %s)\n' "$dir" "$jars" "$ver"
  exit 0
fi
if [ "$jars" -gt 0 ]; then
  printf '  old     %s (have %s, want %s) — replacing jars\n' "$dir" "${have:-unknown}" "$ver"
  find "$dir" -name '*.jar' -delete
fi

url="https://hub-downloads.confluent.io/api/plugins/confluentinc/kafka-connect-avro-converter/versions/${ver}/confluentinc-kafka-connect-avro-converter-${ver}.zip"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

printf '  from    %s\n' "$url"
curl -fL --retry 5 --retry-all-errors --connect-timeout 30 \
  --progress-bar -o "${tmp}/avro-converter.zip" "$url"
python3 - "$tmp/avro-converter.zip" "$tmp/extracted" <<'PY'
import sys
import zipfile
from pathlib import Path
src, dest = Path(sys.argv[1]), Path(sys.argv[2])
dest.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(src) as zf:
    zf.extractall(dest)
PY
find "$tmp/extracted" -name '*.jar' -exec cp {} "$dir" \;
jars=$(find "$dir" -name '*.jar' | wc -l)
if [ "$jars" -eq 0 ]; then
  printf '  FAIL    no jars extracted into %s\n' "$dir" >&2
  exit 1
fi
printf '%s\n' "$ver" > "$stamp"
chmod -R a+rX "$dir"
printf '  ok      %s (%s jars, %s)\n' "$dir" "$jars" "$ver"
