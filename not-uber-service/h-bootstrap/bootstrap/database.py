"""Creating the application's own database and schema, before anything else.

Every other module in this stack — nus_common.postgres included — connects
to whatever PG_DATABASE says. That is a problem for exactly one step: the
very first thing this container does. On a brand new cluster, PG_DATABASE
(now "nus", not Postgres's default "postgres") does not exist yet, so the
pooled connections nus_common.postgres hands out would fail before bootstrap
even gets to wait_for(postgres.ping, ...).

This module solves it with one raw, unpooled connection to the "postgres"
database that always exists, used only to create "nus" and point it at its
own schema. After that, every other module in the repo - migrations, the
OSM import, every service's queries - keeps working completely unmodified:
ALTER DATABASE ... SET search_path makes "nus" the default schema for any
future connection to this database, from any client, without a single query
anywhere needing to be schema-qualified.
"""

import psycopg

from nus_common import config
from nus_common.lifecycle import wait_for
from nus_common.logging import get_logger

log = get_logger(__name__)


def _admin_connection_string(dbname: str) -> str:
    host = config.optional("PG_HOST", "nus-lb-a")
    port = config.integer("PG_WRITE_PORT", 5432)
    user = config.optional("PG_USER", "postgres")
    password = config.required("PG_PASSWORD")
    return (
        f"host={host} port={port} dbname={dbname} "
        f"user={user} password={password} connect_timeout=10"
    )


def ensure_ready() -> None:
    """Create the application database and schema, idempotently.

    Runs before anything touches nus_common.postgres's pools. Every check
    here is safe to run again: a restarted or re-run bootstrap must not
    fail just because "nus" already exists from the run before.
    """
    database = config.optional("PG_DATABASE", "postgres")
    user = config.optional("PG_USER", "postgres")

    wait_for(_can_reach_postgres, "PostgreSQL through nus-lb-a (postgres database)")

    with psycopg.connect(_admin_connection_string("postgres"), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if exists:
            log.info("database already exists", extra={"database": database})
        else:
            # CREATE DATABASE cannot run inside a transaction block, hence
            # autocommit=True on this connection.
            conn.execute(f'CREATE DATABASE "{database}" OWNER "{user}"')
            log.info("database created", extra={"database": database})

    with psycopg.connect(_admin_connection_string(database), autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{database}"')
        conn.execute(
            f'ALTER DATABASE "{database}" SET search_path = "{database}", public'
        )
    log.info("schema ready", extra={"database": database, "schema": database})


def _can_reach_postgres() -> bool:
    with psycopg.connect(_admin_connection_string("postgres"), connect_timeout=10) as conn:
        conn.execute("SELECT 1")
    return True
