"""Applying the SQL migrations, exactly once each.

There is no migration framework here on purpose. The whole job is: run the
files in `migrations/` in name order, and remember which ones already ran.
That is a table and a loop, and a framework would only hide it.
"""

from pathlib import Path

from nus_common import postgres
from nus_common.logging import get_logger

log = get_logger(__name__)

# The table that remembers what has already been applied. Created here rather
# than in a migration, because it has to exist before the first one runs.
CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def apply_all(migrations_dir: Path) -> int:
    """Run every migration that has not run yet. Returns how many were new."""
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"no .sql files found in {migrations_dir}")

    applied = 0
    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TRACKING_TABLE)
            cur.execute("SELECT filename FROM schema_migrations")
            already = {row[0] for row in cur.fetchall()}
        conn.commit()

        for path in files:
            if path.name in already:
                log.info("migration already applied", extra={"file": path.name})
                continue

            log.info("applying migration", extra={"file": path.name})
            # One transaction per file: a migration either lands completely
            # or not at all, and the record of it lands with it.
            with conn.cursor() as cur:
                cur.execute(path.read_text())
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
            conn.commit()
            applied += 1

    log.info("migrations done", extra={"new": applied, "total": len(files)})
    return applied
