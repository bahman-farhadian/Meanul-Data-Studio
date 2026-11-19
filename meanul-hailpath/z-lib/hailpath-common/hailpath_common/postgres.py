"""Talking to PostgreSQL.

Two rules the whole stack follows:

1. Clients use `hailpath-postgres`, not a proxy. There is one PostgreSQL
   and one listen. Debezium uses that port.
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

from hailpath_common import config
from hailpath_common.logging import get_logger

log = get_logger(__name__)

_pool: ConnectionPool | None = None


def _connection_string(port: int) -> str:
    host = config.optional("PG_HOST", "hailpath-postgres")
    database = config.optional("PG_DATABASE", "postgres")
    user = config.optional("PG_USER", "postgres")
    password = config.required("PG_PASSWORD")
    # connect_timeout keeps a dead Postgres from turning into a hung service.
    # statement_timeout is the same idea for a single query: pgr_ksp
    # (hailpath_common.routing.route) is a real graph search over a real street
    # network, and confirmed live, some specific call can run away and
    # exhaust a node's memory rather than just running slowly - a plain
    # pgr_dijkstra call is 50-150ms, so 30s is generous headroom for a
    # legitimate call and still a hard ceiling on a runaway one. The
    # caller (routing.route) treats psycopg.errors.QueryCanceled the same
    # way it already treats "no path found" - not a crash.
    return (
        f"host={host} port={port} dbname={database} "
        f"user={user} password={password} connect_timeout=10 "
        f"options='-c statement_timeout=30000'"
    )


def _make_pool(port: int, size: int) -> ConnectionPool:
    return ConnectionPool(
        conninfo=_connection_string(port),
        min_size=1,
        max_size=size,
        # Hand out a connection that has been checked, so a caller never gets
        # one the server already dropped.
        check=ConnectionPool.check_connection,
        open=True,
        # pgr_ksp (hailpath_common.routing.route, called continuously by
        # dispatch-service for live trips and by h-bootstrap's historical
        # routing burst) is a pgRouting/Boost Graph Library function -  it
        # manages its own memory outside Postgres's tracked allocator.
        # Confirmed live: pg_log_backend_memory_contexts() showed a few KB
        # of Postgres-tracked memory on a backend whose OS-level RSS had
        # grown to hundreds of MB, and a real run OOM-killed the process at
        # ~15GB for a single backend within about 40 minutes. The pool's
        # own 3600s default gave a connection a full hour before recycling
        # - far longer than that. Recycling every 5 minutes bounds the
        # worst case regardless of what pgr_ksp leaks internally, for
        # every caller of this pool, not just bootstrap's own burst.
        max_lifetime=300.0,
    )


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        port = config.integer("PG_PORT", 5432)
        _pool = _make_pool(port, config.integer("PG_POOL_SIZE", 5))
        log.info("postgres pool ready", extra={"port": port})
    return _pool


@contextmanager
def write_connection():
    """A connection that can change data.

        with write_connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO ...", params)

    The transaction is committed when the block ends without an error, and
    rolled back if something is raised.
    """
    with _get_pool().connection() as conn:
        yield conn


@contextmanager
def read_connection():
    """A connection from the same pool as write_connection.

    Use write_connection to read a row inside the transaction that wrote it.
    """
    with _get_pool().connection() as conn:
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
    """True when Postgres answers. Used by the waiting helpers."""
    with write_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() is not None


def reset_pools() -> None:
    """Drop cached pools so the next checkout reads PG_* from the environment.

    Tests point PG_HOST/PG_PORT at a throwaway node after this module
    has already been imported. Without this, the first checkout in the
    process would pin every later test to the wrong host.
    """
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = None
