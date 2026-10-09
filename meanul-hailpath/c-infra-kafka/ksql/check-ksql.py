#!/usr/bin/env python3
"""Prove ksqlDB holds everything ksql/*.sql declares, and that it is running.

The bar this replaces was "the server answers /info", which a server with
nothing in it passes perfectly. DBeaver connecting and showing an empty
Streams folder was the visible symptom; the check said ok throughout.

The expected names are READ OUT OF THE DDL, never listed here. A check with
its own copy of the list passes the day someone adds a stream and forgets
to add it in two places - which is the failure it exists to catch.

Four things fail the run:

  * a declared stream or table that ksqlDB does not have
  * nothing declared at all, or nothing registered at all
  * a persistent query that is not RUNNING
  * a server setting that makes the streams unreadable from a SQL client

The third would otherwise hide. A CTAS whose query has died leaves the
table listed and visible, answering with state frozen at the moment it
stopped - a dashboard reading it sees numbers, just not current ones.

The fourth is here because "the stream exists" and "a person can read it"
turned out to be different claims. ksqlDB ships pull queries over streams
and table scans DISABLED, so `SELECT * FROM trip_requests;` is refused and
`... EMIT CHANGES` without a LIMIT never terminates - which reaches the
client as a bare TimeoutException naming nothing. Registering the streams
and leaving those settings alone passed every other check here while
DBeaver showed an error and no data.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

KSQL_URL = os.environ.get("KSQL_URL", "http://localhost:8088")
KSQL_USER = os.environ.get("KSQLDB_ADMIN_USER", "admin")
KSQL_PASSWORD = os.environ["KSQLDB_ADMIN_PASSWORD"]
KSQL_DIR = pathlib.Path(os.environ.get("KSQL_DIR", "/ksql"))

DECLARED = re.compile(
    r"(?im)^\s*CREATE\s+(STREAM|TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)"
)


def call(path: str, payload: dict | None = None, timeout: int = 20):
    request = urllib.request.Request(
        KSQL_URL.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Content-Type": "application/vnd.ksql.v1+json",
            "Authorization": "Basic "
            + base64.b64encode(f"{KSQL_USER}:{KSQL_PASSWORD}".encode()).decode(),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def show(what: str) -> list[dict]:
    body = call("/ksql", {"ksql": f"SHOW {what};"})
    return body[0].get(what.lower(), []) if body else []


# The settings that decide whether a SQL client can read any of this, and
# the value each has to hold. All three ship as something else.
CLIENT_SETTINGS = {
    "ksql.query.pull.stream.enabled": "true",
    "ksql.query.pull.table.scan.enabled": "true",
    "ksql.streams.auto.offset.reset": "earliest",
}


def properties() -> dict[str, str]:
    body = call("/ksql", {"ksql": "SHOW PROPERTIES;"})
    if not body:
        return {}
    return {
        item["name"]: str(item.get("value"))
        for item in body[0].get("properties", [])
    }


def declared() -> dict[str, set[str]]:
    """Every name ksql/*.sql says should exist, uppercased as ksqlDB stores it."""
    found: dict[str, set[str]] = {"STREAM": set(), "TABLE": set()}
    for path in sorted(KSQL_DIR.glob("*.sql")):
        text = "\n".join(
            line for line in path.read_text().splitlines()
            if not line.lstrip().startswith("--")
        )
        for kind, name in DECLARED.findall(text):
            found[kind.upper()].add(name.upper())
    return found


def main() -> int:
    errors: list[str] = []

    info = call("/info").get("KsqlServerInfo", {})
    print(f"ksqlDB {info.get('version', '?')}  service id {info.get('ksqlServiceId', '?')}")
    print(f"cluster {info.get('kafkaClusterId', '?')}")
    print()

    want = declared()
    if not (want["STREAM"] or want["TABLE"]):
        print(f"FAIL\n  nothing declared under {KSQL_DIR} - the check cannot pass vacuously")
        return 1

    for kind, plural in (("STREAM", "STREAMS"), ("TABLE", "TABLES")):
        registered = {item["name"]: item for item in show(plural)}
        print(f"{plural.lower()} registered: {len(registered)}, declared: {len(want[kind])}")
        for name in sorted(want[kind]):
            item = registered.get(name)
            if item is None:
                print(f"  MISSING  {name}")
                errors.append(f"{kind.lower()} {name} is declared but not registered")
                continue
            print(f"  ok       {name:<26} <- {item['topic']}  ({item['valueFormat']})")
        for name in sorted(set(registered) - want[kind]):
            print(f"  extra    {name}  (registered but not in ksql/*.sql)")
        print()

    effective = properties()
    print("settings a SQL client depends on:")
    for name, want in CLIENT_SETTINGS.items():
        got = effective.get(name)
        mark = "ok     " if got == want else "WRONG  "
        print(f"  {mark}  {name:<38} = {got!r}")
        if got != want:
            errors.append(
                f"{name} is {got!r}, not {want!r} - a client opening a stream "
                "is refused or hangs"
            )
    print()

    queries = show("QUERIES")
    print(f"persistent queries: {len(queries)}")
    for query in queries:
        state = query.get("state") or "UNKNOWN"
        sinks = ", ".join(query.get("sinks") or [])
        print(f"  {state:<10} {query.get('id')}  -> {sinks}")
        if state != "RUNNING":
            errors.append(f"query {query.get('id')} is {state}, not RUNNING")
    print()

    if errors:
        print("FAIL")
        for err in errors:
            print(f"  {err}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"FAIL\n  ksqlDB did not answer at {KSQL_URL}: {exc}")
        sys.exit(1)
