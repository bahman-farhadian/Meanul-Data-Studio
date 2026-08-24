"""Talking to PostgreSQL through the entry tier.

Two rules the whole stack follows:

1. **Never connect to a pg-* container by name.** The leader is elected and
   moves on failover; only the proxies know where it is. Writes go to
   lb-a:5432 (the current leader) and reads to lb-a:5433 (the replicas).
2. **Only the components that own data come here at all.** Everything else
   reads from Redis. See section 1 of the main README.

Connections are pooled. Opening a PostgreSQL connection is not cheap, and a
generator that opens one per event would spend more time connecting than
working.
"""

from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from nus_common import config
from nus_common.logging import get_logger

log = get_logger(__name__)

_write_pool: ConnectionPool | None = None
_read_pool: ConnectionPool | None = None


def _connection_string(port: int) -> str:
    host = config.optional("PG_HOST", "lb-a")
    database = config.optional("PG_DATABASE", "postgres")
    user = config.optional("PG_USER", "postgres")
    password = config.required("PG_PASSWORD")
    # connect_timeout keeps a dead proxy from turning into a hung service.
    return (
        f"host={host} port={port} dbname={database} "
        f"user={user} password={password} connect_timeout=10"
    )


def _pool(port: int, size: int) -> ConnectionPool:
    return ConnectionPool(
        conninfo=_connection_string(port),
        min_size=1,
        max_size=size,
        # Hand out a connection that has been checked, so a caller never gets
        # one that died while the proxy moved to a new leader.
        check=ConnectionPool.check_connection,
        open=True,
    )


@contextmanager
def write_connection():
    """A connection that can change data. Goes to the current leader.

    Used as:

        with write_connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO ...", params)

    The transaction is committed when the block ends without an error, and
    rolled back if something is raised.
    """
    global _write_pool
    if _write_pool is None:
        port = config.integer("PG_WRITE_PORT", 5432)
        _write_pool = _pool(port, config.integer("PG_POOL_SIZE", 5))
        log.info("postgres write pool ready", extra={"port": port})

    with _write_pool.connection() as conn:
        yield conn


@contextmanager
def read_connection():
    """A read-only connection. Goes to the replica pool.

    The replicas can be a moment behind the leader. That is fine for
    reporting and lookups, and wrong for reading back something just written
    - use write_connection for that.
    """
    global _read_pool
    if _read_pool is None:
        port = config.integer("PG_READ_PORT", 5433)
        _read_pool = _pool(port, config.integer("PG_POOL_SIZE", 5))
        log.info("postgres read pool ready", extra={"port": port})

    with _read_pool.connection() as conn:
        yield conn


def fetch_all(conn: Connection, sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Run a query and return the rows as dictionaries.

    Dictionaries rather than tuples on purpose: row["driver_id"] survives a
    column being added to the query, row[3] does not.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(conn: Connection, sql: str, params: tuple | dict | None = None) -> dict | None:
    """Run a query and return the first row, or None."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def ping() -> bool:
    """True when the write path answers. Used by the waiting helpers."""
    with write_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() is not None
