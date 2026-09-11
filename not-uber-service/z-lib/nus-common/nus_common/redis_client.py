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


def vehicle_key(driver_id: str) -> str:
    """The vehicle a driver drives. Written by cache-updater from cdc.vehicles.

    A separate key from driver_key rather than a merged one: two CDC streams
    (cdc.drivers, cdc.vehicles) writing into one key would need read-modify-
    write and a defined merge order, breaking cache-updater's "safe to
    replay, one row overwrites one key" invariant. One vehicle per driver
    today, so keying by driver_id is enough.
    """
    return f"vehicle:{driver_id}"


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


# Kept in sync with vehicles.vehicle_type's CHECK constraint
# (h-bootstrap/migrations/005_vehicles.sql).
VEHICLE_TYPES = ("economy", "xl", "premium")


def geo_available_drivers_key(vehicle_type: str) -> str:
    """The free-driver GEO set for one vehicle tier.

    One set per tier, not one flat set filtered afterwards: Redis GEO has no
    secondary filter, and searching each tier separately is what lets
    dispatch match "this rider wants XL" to a driver who actually has one,
    instead of finding the nearest driver of any tier and hoping.
    """
    return f"geo:drivers:available:{vehicle_type}"


# How long a hotspot score stays valid: six hours, the length of one period.
HOTSPOT_TTL_SECONDS = 6 * 60 * 60


# --------------------------------------------------------------------------
# Which of Redis's 16 numbered logical databases each domain lives in.
# --------------------------------------------------------------------------
# Redis Sentinel (unlike Cluster mode) fully supports these, and until now
# nothing here used more than db0. Splitting by domain makes "how many
# drivers are free right now" a `redis-cli -n 1 DBSIZE` instead of a
# manual scan - exactly what diagnosing dispatch saturation needed earlier
# in this project and had to do by hand. Worth knowing honestly:
# maxmemory-policy is instance-wide, not per-db, so this buys operational
# clarity and blast-radius isolation, not independent eviction tuning.
DB_SYSTEM = 0     # system:bootstrap:done - the one thing every service checks
DB_DRIVER = 1     # driver:*, vehicle:*, geo:drivers:available:*
DB_PASSENGER = 2  # passenger:*
DB_TRIP = 3       # trip:*, trip:*:active
DB_DEMAND = 4     # hotspot:*, zone:* (reference data, grouped with demand -
                  # neither is any single service's own domain)


# --------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------

def _sentinel() -> Sentinel:
    """Build the Sentinel connection from the environment."""
    hosts = config.optional(
        "REDIS_SENTINELS",
        "nus-sentinel-1:26379,nus-sentinel-2:26379,nus-sentinel-3:26379",
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


def primary(db: int = DB_SYSTEM) -> Redis:
    """A connection to whichever Redis node is primary right now, for one
    numbered database (see the DB_* constants above).

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
        db=db,
    )


def replica(db: int = DB_SYSTEM) -> Redis:
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
        db=db,
    )
