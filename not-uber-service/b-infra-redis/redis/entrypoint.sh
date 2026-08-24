#!/bin/sh
# Materialise the live redis.conf once, then hand over to redis-server.
#
# Why a copy instead of the mounted template: Sentinel reconfigures nodes at
# runtime (REPLICAOF + CONFIG REWRITE on every promotion/demotion) and Redis
# must be able to persist that into its own config file. The copy lives on the
# node's data volume, so the topology survives restarts.
#
# Consequence to know: after the first start, edits to the template and to
# REDIS_PASSWORD/REDIS_MAXMEMORY have no effect on that node. Either edit
# /data/redis.conf in place (docker compose exec) or drop the node's volume.
set -eu

CONF=/data/redis.conf

if [ ! -f "$CONF" ]; then
    echo "entrypoint: materialising $CONF for ${REDIS_NODE}"
    # Secrets and per-node values are appended rather than substituted into
    # the template: no sed, so no delimiter or escaping hazard in passwords.
    cp /templates/redis.conf "$CONF"
    {
        printf '\n# ---- appended by entrypoint.sh on first start ----\n'
        # Sentinel records replicas by the address they announce. Container IPs
        # change on recreate, so announce the stable service hostname instead.
        printf 'replica-announce-ip %s\n' "$REDIS_NODE"
        printf 'replica-announce-port 6379\n'
        printf 'requirepass %s\n' "$REDIS_PASSWORD"
        # masterauth: the same secret, used when this node replicates from
        # whichever peer Sentinel has made primary.
        printf 'masterauth %s\n' "$REDIS_PASSWORD"
        printf 'maxmemory %s\n' "$REDIS_MAXMEMORY"
    } >> "$CONF"

    # Seed topology only: redis-1 starts as primary, the others follow it.
    # From the first Sentinel failover on, the rewritten config decides.
    if [ -n "${REDIS_REPLICAOF:-}" ]; then
        printf 'replicaof %s\n' "$REDIS_REPLICAOF" >> "$CONF"
    fi
else
    echo "entrypoint: $CONF exists, keeping it (Sentinel owns this file now)"
fi

exec redis-server "$CONF"
