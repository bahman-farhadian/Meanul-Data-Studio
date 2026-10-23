#!/usr/bin/env python3
"""Prove the Registry holds the schemas in this directory, not older ones.

`make schemas` registers every .avsc here. Nothing afterwards checks that
it worked, and until now the only way to read the answer was a hand-typed
`docker exec ... curl` against :8081 - which is not exposed through
hailpath-proxy, and which the schema-registry image cannot even run
against itself: it ships neither curl nor wget. Step 12c's Avro bar was
the one bar in that list with no target behind it.

"The subject exists" is the weak version of this question. A subject
registered by an older copy of a schema also exists, and every producer
is then checked for compatibility against the wrong contract - the exact
failure register-schemas.py was written to prevent. So this compares what
came back with the file on disk: namespace, record name, and the field
list in order. A mismatch means the Registry is behind this repository,
and `make schemas` is the fix.

The expected subjects are READ OUT OF THE DIRECTORY, never listed here,
for the reason check-ksql.py gives: a check carrying its own copy of the
list passes the day someone adds a schema and forgets the second place.

Subjects this repository does not own - Debezium's cdc.* keys and values,
its heartbeat, ksqlDB's internals - are counted and otherwise left alone.
They are not ours to assert anything about.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

REGISTRY_URL = os.environ.get("SCHEMA_REGISTRY_URL", "http://hailpath-schema-registry:8081")
SCHEMA_DIR = pathlib.Path(os.environ.get("SCHEMA_DIR", "/schemas"))


def call(path: str, timeout: int = 15):
    request = urllib.request.Request(REGISTRY_URL.rstrip("/") + path)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def shape(schema: dict) -> tuple[str, str, tuple[str, ...]]:
    """The part of a record schema a producer's contract depends on."""
    return (
        schema.get("namespace", ""),
        schema.get("name", ""),
        tuple(field.get("name", "") for field in schema.get("fields", [])),
    )


def main() -> int:
    files = sorted(SCHEMA_DIR.glob("*.avsc"))
    if not files:
        print(f"FAIL\n  no .avsc files in {SCHEMA_DIR} - nothing to check")
        return 1

    subjects = set(call("/subjects"))
    errors: list[str] = []

    print("== every .avsc in this repository, against the Registry ==")
    for path in files:
        subject = f"{path.stem}-value"
        if subject not in subjects:
            print(f"  FAIL     {subject:<34} not registered")
            errors.append(f"{subject} is not in the Registry - run make schemas")
            continue

        latest = call(f"/subjects/{subject}/versions/latest")
        registered = json.loads(latest["schema"])
        on_disk = json.loads(path.read_text())
        namespace, name, fields = shape(registered)

        if shape(registered) != shape(on_disk):
            print(f"  FAIL     {subject:<34} differs from {path.name}")
            errors.append(
                f"{subject} version {latest['version']} is {namespace}.{name} with "
                f"{len(fields)} fields; {path.name} declares "
                f"{on_disk.get('namespace')}.{on_disk.get('name')} with "
                f"{len(on_disk.get('fields', []))} - run make schemas"
            )
            continue

        print(
            f"  ok       {subject:<34} id {latest['id']:<4} "
            f"version {latest['version']}  {namespace}.{name}  {len(fields)} fields"
        )

    print()
    print(f"subjects in the Registry: {len(subjects)}, {len(files)} of them declared here")
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
        print(f"FAIL\n  Schema Registry did not answer at {REGISTRY_URL}: {exc}")
        sys.exit(1)
