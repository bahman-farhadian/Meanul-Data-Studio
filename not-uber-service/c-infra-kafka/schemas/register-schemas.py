#!/usr/bin/env python3
"""Register every .avsc in this directory, before anything produces.

Until this existed, the value schema of each topic was whatever the FIRST
producer to connect happened to send. That is a contract established by
accident: a service shipping a stale .avsc sets the baseline, and every
later producer is then checked for compatibility against the wrong thing.
The schemas are in this repository; this registers them from here, the same
way topics/create-topics.sh creates the topics and e-infra-clickhouse's
apply-ddl.sh creates the tables.

It also makes ksqlDB possible at all. The streams in ../ksql/ declare only
their key column and let ksqlDB read the value columns out of the Registry,
which is what keeps this repository from holding a fourth copy of every
schema. On a stack that has just been destroyed and brought back up, the
Registry is empty until a producer runs - and the producers start after
ksql-init. Registering here, right after the topics are created, is what
makes the order work.

Subjects are named <topic>-value, which is Confluent's default
TopicNameStrategy and exactly what the producers use: nus_common.kafka
builds its AvroSerializer with a SerializationContext of (topic, VALUE).
The file's own name is the topic name, so the two cannot drift.

Re-runnable. Registering a schema that is already there returns the id it
already has - the Registry deduplicates on the schema itself, not on the
request - so this does not create a second version of anything.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

REGISTRY_URL = os.environ.get("SCHEMA_REGISTRY_URL", "http://nus-schema-registry:8081")
SCHEMA_DIR = pathlib.Path(os.environ.get("SCHEMA_DIR", "/schemas"))
CONTENT_TYPE = "application/vnd.schemaregistry.v1+json"


def call(path: str, payload: dict | None = None, timeout: int = 15):
    request = urllib.request.Request(
        REGISTRY_URL.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": CONTENT_TYPE},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def wait_for_registry(attempts: int = 30, delay: int = 5) -> None:
    print(f"waiting for Schema Registry at {REGISTRY_URL} ...")
    for attempt in range(1, attempts + 1):
        try:
            call("/subjects", timeout=5)
            print("Schema Registry is answering")
            return
        except Exception as exc:  # noqa: BLE001 - anything here means "not ready"
            print(f"  not ready yet (attempt {attempt}/{attempts}): {type(exc).__name__}")
            time.sleep(delay)
    print("Schema Registry never answered - is it up?", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    wait_for_registry()

    files = sorted(SCHEMA_DIR.glob("*.avsc"))
    if not files:
        print(f"no .avsc files in {SCHEMA_DIR} - nothing to register", file=sys.stderr)
        raise SystemExit(1)

    before = set(call("/subjects"))
    for path in files:
        subject = f"{path.stem}-value"
        # The schema goes up as a STRING inside a JSON object. That is the
        # Registry's own wire format and not a quirk of this script.
        payload = {"schemaType": "AVRO", "schema": path.read_text()}
        try:
            result = call(f"/subjects/{subject}/versions", payload)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            print(f"  FAILED  {subject}", file=sys.stderr)
            print(f"  HTTP {exc.code}: {body}", file=sys.stderr)
            raise SystemExit(1)
        mark = "=" if subject in before else "+"
        print(f"  {mark}  {subject:<34} id {result['id']}  version {result['version']}")

    print()
    print(f"subjects now registered: {len(call('/subjects'))}")
    for subject in sorted(call("/subjects")):
        print(f"  {subject}")


if __name__ == "__main__":
    main()
