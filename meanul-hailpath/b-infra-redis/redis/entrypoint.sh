#!/bin/sh
# Copy the template to the data volume once, then start redis-server.
#
# The copy is on the data volume so requirepass and maxmemory survive a
# restart. The template stays free of the password. After the first start,
# a password change means editing /data/redis.conf or dropping the volume.
set -eu

CONF=/data/redis.conf

if [ ! -f "$CONF" ]; then
    echo "entrypoint: materialising $CONF"
    cp /templates/redis.conf "$CONF"
    {
        printf '\n# ---- appended by entrypoint.sh on first start ----\n'
        printf 'requirepass %s\n' "$REDIS_PASSWORD"
        printf 'maxmemory %s\n' "$REDIS_MAXMEMORY"
    } >> "$CONF"
else
    echo "entrypoint: $CONF exists, keeping it"
fi

exec redis-server "$CONF"
