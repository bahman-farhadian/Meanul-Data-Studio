#!/bin/sh
# Materialise the live sentinel.conf once, then hand over to redis-sentinel.
#
# The appended monitor block is the SEED topology only: it names redis-1 as
# the master. After the first failover Sentinel rewrites that line itself, and
# the file below is kept as-is on every later start — which is exactly why the
# config lives on a volume instead of being regenerated each time.
set -eu

CONF=/data/sentinel.conf

if [ ! -f "$CONF" ]; then
    echo "entrypoint: materialising $CONF for ${SENTINEL_NODE}"
    cp /templates/sentinel.conf "$CONF"
    {
        printf '\n# ---- appended by entrypoint.sh on first start ----\n'
        # Announce a hostname, not the container IP, for the same reason the
        # data nodes do: IPs are recycled across recreates.
        printf 'sentinel announce-ip %s\n' "$SENTINEL_NODE"
        printf 'sentinel announce-port 26379\n'
        # Order matters: `monitor` first, everything naming the master after.
        printf 'sentinel monitor %s %s 6379 %s\n' \
            "$REDIS_MASTER_NAME" "$REDIS_MASTER_HOST" "$SENTINEL_QUORUM"
        printf 'sentinel auth-pass %s %s\n' "$REDIS_MASTER_NAME" "$REDIS_PASSWORD"
        printf 'sentinel down-after-milliseconds %s %s\n' \
            "$REDIS_MASTER_NAME" "$SENTINEL_DOWN_AFTER_MS"
        printf 'sentinel failover-timeout %s %s\n' \
            "$REDIS_MASTER_NAME" "$SENTINEL_FAILOVER_TIMEOUT_MS"
        # One replica resynced at a time: a full sync is expensive, and doing
        # both at once would leave no warm replica to read from.
        printf 'sentinel parallel-syncs %s 1\n' "$REDIS_MASTER_NAME"
    } >> "$CONF"
else
    echo "entrypoint: $CONF exists, keeping it (it holds the live topology)"
fi

exec redis-sentinel "$CONF"
