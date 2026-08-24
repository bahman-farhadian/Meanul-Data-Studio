"""Talking to Redis, and the key names the whole stack agreed on.

Redis is the read path for everything except the components that own the
data. Nothing here reaches into PostgreSQL.

Sentinel matters for how a connection is made: the primary is elected, so no
service may remember an address. Instead a service asks Sentinel "who is the
primary right now" and reconnects when the answer changes. The redis library
does that for us as long as connections are taken from a Sentinel object,
which is what this module returns.
"""

from redis import Redis
from redis.sentinel import Sentinel

from nus_common import config
from nus_common.logging import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------
# Key names
# --------------------------------------------------------------------------
# Written down once, here, because a typo in a key name is invisible: the
# writer stores something nobody reads and the reader finds nothing, and
# neither of them fails.

def driver_key(driver_id: str) -> str:
    """Profile and current status of one driver. Written by cache-updater."""
    return f"driver:{driver_id}"


def passenger_key(passenger_id: str) -> str:
    """Profile of one passenger. Written by cache-updater."""
    return f"passenger:{passenger_id}"


def trip_active_key(trip_id: str) -> str:
    """State of a trip while it runs, including route and predicted duration.

    Written by dispatch-service, read by the generators and the sink.
    """
    return f"trip:{trip_id}:active"


def trip_key(trip_id: str) -> str:
    """The stored trip row, as it is in PostgreSQL. Written by cache-updater.

    Not the same thing as trip_active_key: this is the database record, that
    one is the live state dispatch keeps while the trip is running.
    """
    return f"trip:{trip_id}"


def zone_key(zone_id: str) -> str:
    """One city zone. Written by cache-updater, read by anything that needs
    the name or the middle of a zone without asking PostgreSQL."""
    return f"zone:{zone_id}"


def hotspot_key(zone_id: str, period: str) -> str:
    """Demand score of one zone in one part of the day.

    Written by city-service with a six-hour lifetime, read by the drivers
    (where should I go) and by dispatch (what should this cost).
    """
    return f"hotspot:{zone_id}:{period}"


# Redis GEO set of drivers that are free right now. dispatch-service asks it
# for the nearest driver to a pickup point.
GEO_AVAILABLE_DRIVERS = "geo:drivers:available"

# How long a hotspot score stays valid: six hours, the length of one period.
HOTSPOT_TTL_SECONDS = 6 * 60 * 60


# --------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------

def _sentinel() -> Sentinel:
    """Build the Sentinel connection from the environment."""
    hosts = config.optional(
        "REDIS_SENTINELS", "sentinel-1:26379,sentinel-2:26379,sentinel-3:26379"
    )
    pairs = []
    for entry in hosts.split(","):
        host, _, port = entry.strip().partition(":")
        pairs.append((host, int(port or 26379)))

    return Sentinel(
        pairs,
        # Short timeouts on purpose: a slow answer from a failing node should
        # become "ask the next Sentinel", not a stuck service.
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )


def primary() -> Redis:
    """A connection to whichever Redis node is primary right now.

    Use it for writes. After a failover the library asks Sentinel again and
    reconnects on its own; the caller only sees one failed command.
    """
    return _sentinel().master_for(
        config.optional("REDIS_MASTER_NAME", "nus-cache"),
        password=config.required("REDIS_PASSWORD"),
        # Values come back as text instead of bytes, which is what every
        # caller in this stack wants.
        decode_responses=True,
        socket_timeout=2.0,
        health_check_interval=30,
    )


def replica() -> Redis:
    """A connection to a replica, for reads that may be a moment behind.

    Replication is fast but not instant, so anything that must see its own
    write should use primary() instead.
    """
    return _sentinel().slave_for(
        config.optional("REDIS_MASTER_NAME", "nus-cache"),
        password=config.required("REDIS_PASSWORD"),
        decode_responses=True,
        socket_timeout=2.0,
        health_check_interval=30,
    )
