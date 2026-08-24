#!/bin/sh
# Create the analytics tables on the cluster.
#
# Every file in this directory is applied in name order, and every statement
# uses IF NOT EXISTS, so running this again changes nothing. That makes it
# the normal way to roll out a new table: add the file, run the one-shot.
#
# What it does NOT do: change a table that already exists. Altering a live
# table is a decision to take by hand, with the data in front of you.
set -eu

CH_HOST="${CH_HOST:-ch-s1r1}"
CH_USER="${CH_USER:-default}"
CH_PASSWORD="${CH_PASSWORD:-}"

client() {
    clickhouse-client --host "$CH_HOST" --user "$CH_USER" --password "$CH_PASSWORD" "$@"
}

echo "waiting for ClickHouse at ${CH_HOST} ..."
attempt=1
while [ "$attempt" -le 30 ]; do
    if client --query "SELECT 1" >/dev/null 2>&1; then
        break
    fi
    echo "  not ready yet (attempt ${attempt}/30)"
    attempt=$(( attempt + 1 ))
    sleep 5
done
if [ "$attempt" -gt 30 ]; then
    echo "ClickHouse never answered - is the cluster up?" >&2
    exit 1
fi

# All four nodes must be visible before ON CLUSTER statements are sent,
# otherwise the missing node only catches up later and the first checks look
# wrong for no good reason.
echo "cluster members ClickHouse can see right now:"
client --query "SELECT host_name, shard_num, replica_num FROM system.clusters WHERE cluster = 'nus_cluster' FORMAT PrettyCompact"

for file in /ddl/*.sql; do
    echo "applying $(basename "$file") ..."
    client --multiquery < "$file"
done

echo
echo "tables now on the cluster:"
client --query "SELECT database, name, engine FROM system.tables WHERE database = 'nus' ORDER BY name FORMAT PrettyCompact"
