#!/bin/sh
# Copy the template to the data volume once, then start redis-server.
#
# requirepass and maxmemory are rewritten from the environment on every
# start, so REDIS_PASSWORD in .env is what Redis actually uses. The
# template stays free of the password.
set -eu

CONF=/data/redis.conf

if [ ! -f "$CONF" ]; then
    echo "entrypoint: materialising $CONF"
    cp /templates/redis.conf "$CONF"
fi

# .env is the source of truth. A leftover requirepass from the first start
# must not outlive REDIS_PASSWORD.
tmp=$(mktemp)
grep -vE '^(requirepass|maxmemory) ' "$CONF" > "$tmp"
{
    printf 'requirepass %s\n' "$REDIS_PASSWORD"
    printf 'maxmemory %s\n' "$REDIS_MAXMEMORY"
} >> "$tmp"
mv "$tmp" "$CONF"

exec redis-server "$CONF"
