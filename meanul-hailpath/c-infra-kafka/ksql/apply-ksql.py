#!/usr/bin/env python3
"""Register every stream and table this stack declares, on a live ksqlDB.

The same contract as e-infra-clickhouse/ddl/apply-ddl.sh: every file in this
directory is applied in name order, every statement is written so that
running it again changes nothing, and adding a stream means adding it to a
file and running the one-shot - not typing it into a SQL client, which
leaves no record of what the cluster is supposed to contain.

python3 rather than curl or the ksqlDB CLI: the cp-ksqldb-server image has
no curl, no wget and no nc - the same finding that shaped this cluster's
healthchecks - and python3 is already how ksqlDB is probed everywhere else
here. Using the server image means no extra pinned tag for a one-shot.

WHAT COUNTS AS AN ERROR

ksqlDB answers a CREATE ... IF NOT EXISTS for something that already exists
with HTTP 200 and a body of type "warning_entity" - not an error, and not a
success either. That is the idempotent path and it prints as "left
untouched", exactly the way create-topics.sh reports a topic it did not
need to make. An HTTP error is fatal and stops the run, because a stream
that failed to register is a stream DBeaver will not show and a query
downstream will not find.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

KSQL_URL = os.environ.get("KSQL_URL", "http://nus-ksqldb-server:8088")
KSQL_USER = os.environ.get("KSQLDB_ADMIN_USER", "admin")
KSQL_PASSWORD = os.environ["KSQLDB_ADMIN_PASSWORD"]
KSQL_DIR = pathlib.Path(os.environ.get("KSQL_DIR", "/ksql"))

# Every statement here reads a topic from its beginning. Without it a stream
# created after traffic started would silently skip everything already on
# the topic, and the first window of the funnel table would be empty for no
# visible reason.
STREAMS_PROPERTIES = {"auto.offset.reset": "earliest"}


def _auth_header() -> str:
    raw = f"{KSQL_USER}:{KSQL_PASSWORD}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def call(path: str, payload: dict | None = None, timeout: int = 60):
    """POST when there is a payload, GET when there is not.

    /info answers a GET and refuses a POST, so the readiness probe and the
    statement calls cannot share one verb.
    """
    request = urllib.request.Request(
        KSQL_URL.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Content-Type": "application/vnd.ksql.v1+json",
            "Authorization": _auth_header(),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def run(statement: str) -> list[dict]:
    return call("/ksql", {"ksql": statement, "streamsProperties": STREAMS_PROPERTIES})


def wait_for_server(attempts: int = 30, delay: int = 5) -> None:
    print(f"waiting for ksqlDB at {KSQL_URL} ...")
    for attempt in range(1, attempts + 1):
        try:
            info = call("/info", timeout=5)
            break
        except Exception as exc:  # noqa: BLE001 - any failure here means "not ready"
            print(f"  not ready yet (attempt {attempt}/{attempts}): {type(exc).__name__}")
            time.sleep(delay)
    else:
        print("ksqlDB never answered - is the server up?", file=sys.stderr)
        raise SystemExit(1)

    version = (info or {}).get("KsqlServerInfo", {})
    print(
        "ksqlDB {} is up, service id {}".format(
            version.get("version", "?"), version.get("ksqlServiceId", "?")
        )
    )


def statements(text: str) -> list[str]:
    """Split a file into statements.

    A line comment is dropped before splitting so that a semicolon inside
    one - which this directory's comments do contain - cannot end a
    statement early. No statement here holds a string literal containing a
    semicolon, so splitting on the character is safe; if one ever does, this
    is the function that has to grow a real tokenizer.
    """
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


def apply_file(path: pathlib.Path) -> tuple[int, int]:
    created = skipped = 0
    print(f"applying {path.name} ...")
    for statement in statements(path.read_text()):
        first_line = statement.splitlines()[0].strip()
        try:
            results = run(statement + ";")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            print(f"  FAILED  {first_line}", file=sys.stderr)
            print(f"  HTTP {exc.code}: {body}", file=sys.stderr)
            raise SystemExit(1)

        for result in results:
            if result.get("@type") == "warning_entity":
                print(f"  =  {first_line}  (already there, left untouched)")
                skipped += 1
            else:
                status = (result.get("commandStatus") or {}).get("message", "ok")
                print(f"  +  {first_line}  ({status})")
                created += 1
    return created, skipped


def show(what: str) -> list[dict]:
    body = run(f"SHOW {what};")
    return body[0].get(what.lower(), []) if body else []


def main() -> None:
    wait_for_server()

    files = sorted(KSQL_DIR.glob("*.sql"))
    if not files:
        print(f"no .sql files in {KSQL_DIR} - nothing to register", file=sys.stderr)
        raise SystemExit(1)

    created = skipped = 0
    for path in files:
        made, left = apply_file(path)
        created += made
        skipped += left

    print()
    print(f"{created} statement(s) applied, {skipped} already there")
    print()
    print("streams now registered:")
    for stream in show("STREAMS"):
        print(f"  {stream['name']:<28} <- {stream['topic']}  ({stream['valueFormat']})")
    print("tables now registered:")
    for table in show("TABLES"):
        print(f"  {table['name']:<28} <- {table['topic']}  ({table['valueFormat']})")


if __name__ == "__main__":
    main()
