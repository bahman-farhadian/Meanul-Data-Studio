"""Getting the routable street graph into PostgreSQL.

The graph itself - NYC's official LION street data, filtered to real
drivable streets, with real costs and a real routing topology - is built
once, on the host, by `make lion-prepare`, entirely outside this container
and outside this stack's lifecycle (see h-bootstrap/lion-prepare/). That step
depends on nothing this project generates, so it is cached across every
`make destroy && make up` cycle instead of being repeated.

What runs here is just the fast part: restore that already-built graph into
the live database, skipped entirely if it is already there.
"""

import subprocess
from pathlib import Path

from nus_common import postgres
from nus_common.logging import get_logger

from bootstrap.settings import Settings

log = get_logger(__name__)


def _run(command: list[str]) -> None:
    log.info("running", extra={"command": " ".join(command[:2]) + " ..."})
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(
            "command failed",
            extra={"command": command[0], "stderr": result.stderr[-2000:]},
        )
        raise RuntimeError(f"{command[0]} exited with code {result.returncode}")


def _already_imported() -> bool:
    """True when the routing tables are there and hold data."""
    with postgres.read_connection() as conn:
        row = postgres.fetch_one(
            conn,
            """
            SELECT count(*) AS n
            FROM information_schema.tables
            WHERE table_name IN ('ways', 'ways_vertices_pgr')
            """,
        )
        if not row or row["n"] < 2:
            return False
        row = postgres.fetch_one(conn, "SELECT count(*) AS n FROM ways")
        return bool(row and row["n"] > 0)


def import_map(settings: Settings) -> None:
    """Restore the pre-built routable graph into PostgreSQL."""
    if settings.skip_map_import:
        log.warning("SKIP_MAP_IMPORT is set - routing will not work")
        return

    if _already_imported():
        log.info("street graph already imported, skipping")
        return

    dump = Path(settings.lion_dir) / "routable-graph.dump"
    if not dump.exists() or dump.stat().st_size == 0:
        raise RuntimeError(
            f"{dump} is missing or empty. Run 'make lion-fetch' then "
            "'make lion-prepare' on the host before bootstrapping - both "
            "are also part of 'make prepare'."
        )

    log.info("restoring the routable graph", extra={"dump": str(dump)})
    _run([
        "pg_restore",
        "--host", _pg("PG_HOST", "nus-lb-a"),
        "--port", _pg("PG_WRITE_PORT", "5432"),
        "--dbname", _pg("PG_DATABASE", "nus"),
        "--username", _pg("PG_USER", "postgres"),
        "--no-owner",
        "--no-privileges",
        str(dump),
    ])

    with postgres.read_connection() as conn:
        ways = postgres.fetch_one(conn, "SELECT count(*) AS n FROM ways")
        vertices = postgres.fetch_one(
            conn,
            "SELECT count(*) AS total, count(*) FILTER (WHERE on_main_network) AS main "
            "FROM ways_vertices_pgr",
        )

    connected_pct = round(100.0 * vertices["main"] / vertices["total"], 1) if vertices["total"] else 0.0
    log.info(
        "street graph ready",
        extra={
            "road_segments": ways["n"] if ways else 0,
            "vertices": vertices["total"] if vertices else 0,
            "connected_pct": connected_pct,
        },
    )
    if connected_pct < 90.0:
        log.warning(
            "less than 90% of the graph is in the main connected component - "
            "generated trips will lean on the max_snap_km retry a lot more "
            "than expected",
            extra={"connected_pct": connected_pct},
        )


def _pg(name: str, default: str) -> str:
    """Read one of the PostgreSQL settings, for passing to pg_restore."""
    import os

    return os.environ.get(name, default)
