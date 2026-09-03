"""Getting the New York street map into PostgreSQL as a routable graph.

Four steps, each one skipped when its result is already there:

1. **download** the OpenStreetMap extract, and check it against the md5 file
   published next to it. A half-downloaded map is worse than no map.
2. **cut it down** to the city box with osmium. The published extract covers
   the whole state; the stack only simulates the city, and the smaller file
   makes every later step faster and lighter on memory.
3. **convert** it to the XML form osm2pgrouting reads.
4. **import** it, which creates the `ways` and `ways_vertices_pgr` tables
   pgRouting needs to find a path.

The downloaded files live on a named volume, so a rebuilt container does not
download the map again.
"""

import hashlib
import signal
import subprocess
from pathlib import Path

import requests

from nus_common import postgres
from nus_common.logging import get_logger

from bootstrap.settings import Settings

log = get_logger(__name__)


def _run(command: list[str]) -> None:
    """Run a command, showing its output, and stop the whole run if it fails.

    A negative return code means the process was killed by a signal rather
    than exiting on its own. That distinction matters here: the map import is
    the memory peak of the whole stack, and when the container's limit is too
    low the kernel kills it outright. Reporting only "failed with code -9"
    sends the reader looking for a bug in a tool that was working fine.
    """
    log.info("running", extra={"command": " ".join(command[:3]) + " ..."})
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return

    if result.returncode < 0:
        signal_number = -result.returncode
        name = signal.Signals(signal_number).name if signal_number in {s.value for s in signal.Signals} else "unknown"
        detail = f"killed by {name} (signal {signal_number})"
        if signal_number == signal.SIGKILL:
            detail += (
                "; on this step that is almost always the container's memory "
                "limit. Raise BOOTSTRAP_MEM in .env and run 'make bootstrap' again"
            )
    else:
        detail = f"exited with code {result.returncode}"

    log.error(
        "command failed",
        extra={
            "command": command[0],
            "detail": detail,
            "stderr": result.stderr[-2000:],
        },
    )
    raise RuntimeError(f"{command[0]} {detail}")


def _md5(path: Path) -> str:
    """The md5 sum of a file, read in pieces so a large file fits in memory."""
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(settings: Settings, target: Path) -> None:
    """Download the extract unless a valid copy is already on the volume."""
    expected = None
    try:
        # The md5 file holds "<sum>  <filename>".
        expected = requests.get(settings.osm_md5_url, timeout=60).text.split()[0]
    except Exception as err:  # noqa: BLE001 - the download can still go ahead
        log.warning("could not read the md5 file", extra={"error": str(err)})

    if target.exists():
        if expected is None:
            log.info("map already downloaded, no md5 to check it against")
            return
        if _md5(target) == expected:
            log.info("map already downloaded and correct, skipping")
            return
        log.warning("downloaded map does not match its md5, downloading again")
        target.unlink()

    log.info("downloading the map", extra={"url": settings.osm_url})
    with requests.get(settings.osm_url, stream=True, timeout=1800) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                handle.write(chunk)

    if expected is not None and _md5(target) != expected:
        raise RuntimeError(
            "the downloaded map does not match its published md5 sum. "
            "Delete it from the volume and try again."
        )
    log.info("map downloaded", extra={"megabytes": round(target.stat().st_size / 1e6)})


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
    """Make sure the routable street graph exists in PostgreSQL."""
    if settings.skip_osm:
        log.warning("SKIP_OSM_IMPORT is set - routing will not work")
        return

    if _already_imported():
        log.info("street graph already imported, skipping")
        return

    directory = Path(settings.osm_dir)
    directory.mkdir(parents=True, exist_ok=True)

    full = directory / "region-latest.osm.pbf"
    clipped = directory / "city.osm.pbf"
    as_xml = directory / "city.osm"

    _download(settings, full)

    if not clipped.exists():
        log.info("cutting the map down to the city box")
        _run([
            "osmium", "extract",
            "--bbox",
            f"{settings.min_lon},{settings.min_lat},{settings.max_lon},{settings.max_lat}",
            "--overwrite", "-o", str(clipped), str(full),
        ])

    if not as_xml.exists():
        # osm2pgrouting reads the XML form, not the compressed one.
        log.info("converting the map to the form osm2pgrouting reads")
        _run(["osmium", "cat", "--overwrite", "-o", str(as_xml), str(clipped)])

    log.info("building the routable graph - this is the slow step")
    _run([
        "osm2pgrouting",
        "--file", str(as_xml),
        "--conf", settings.osm2pgrouting_config,
        "--host", _pg("PG_HOST", "lb-a"),
        "--port", _pg("PG_WRITE_PORT", "5432"),
        "--dbname", _pg("PG_DATABASE", "postgres"),
        "--username", _pg("PG_USER", "postgres"),
        "--password", _pg("PG_PASSWORD", ""),
        # Drop anything left behind by an interrupted earlier attempt.
        "--clean",
    ])

    with postgres.read_connection() as conn:
        row = postgres.fetch_one(conn, "SELECT count(*) AS n FROM ways")
        log.info("street graph ready", extra={"road_segments": row["n"] if row else 0})


def _pg(name: str, default: str) -> str:
    """Read one of the PostgreSQL settings, for passing to osm2pgrouting."""
    import os

    return os.environ.get(name, default)
