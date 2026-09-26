"""ksqlDB declares exactly the topics this stack declares, and nothing drifts.

ksqlDB was deployed, authenticated and empty for the whole of this project
so far: DBeaver connected, listed the topics, and showed no streams, because
nothing had ever been registered. The fix is ksql/*.sql, and these are the
checks that keep it true - a new topic without a stream, or a stream over a
topic nobody creates, both fail here rather than on the server.

Everything is read out of the shipped files. Nothing in this module holds
its own copy of the topic list, the field names, or the object names, so a
schema change cannot leave the test passing against a stale expectation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# this file lives at not-uber-service/z-lib/nus-common/tests/
NUS = TESTS.parents[2]

KSQL_DIR = NUS / "c-infra-kafka" / "ksql"
SCHEMA_DIR = NUS / "c-infra-kafka" / "schemas"
TOPICS_TSV = NUS / "c-infra-kafka" / "topics" / "topics.tsv"

# CREATE STREAM|TABLE [IF NOT EXISTS] name  -> the same expression
# check-ksql.py uses. Kept identical on purpose: test_check_regex_matches
# below is what proves it still finds anything at all.
DECLARED = re.compile(
    r"(?im)^\s*CREATE\s+(STREAM|TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)"
)


def _sql_text() -> str:
    """Every ksql file, comment lines removed."""
    out = []
    for path in sorted(KSQL_DIR.glob("*.sql")):
        out.extend(
            line for line in path.read_text().splitlines()
            if not line.lstrip().startswith("--")
        )
    return "\n".join(out)


def _source_streams() -> dict[str, dict[str, str]]:
    """Each CREATE STREAM in 010, as {name: {key_column, topic, props...}}."""
    text = "\n".join(
        line for line in (KSQL_DIR / "010_source_streams.sql").read_text().splitlines()
        if not line.lstrip().startswith("--")
    )
    streams: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r"(?is)CREATE\s+STREAM\s+IF\s+NOT\s+EXISTS\s+([a-z_][a-z0-9_]*)\s*"
        r"\((?P<cols>[^)]*)\)\s*WITH\s*\((?P<props>[^)]*)\)"
    )
    for match in pattern.finditer(text):
        props = dict(
            (k.strip().upper(), v.strip().strip("'"))
            for k, v in (
                part.split("=", 1) for part in match.group("props").split(",") if "=" in part
            )
        )
        props["_columns"] = " ".join(match.group("cols").split())
        streams[match.group(1)] = props
    return streams


def _declared_topics() -> set[str]:
    rows = set()
    for line in TOPICS_TSV.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rows.add(line.split("\t")[0].strip())
    assert rows, f"no topic rows parsed from {TOPICS_TSV}"
    return rows


def _fields(topic: str) -> set[str]:
    schema = json.loads((SCHEMA_DIR / f"{topic}.avsc").read_text())
    return {field["name"] for field in schema["fields"]}


def test_every_declared_topic_has_a_stream() -> None:
    """A topic nobody can query from SQL is the bug this whole file fixes."""
    streams = _source_streams()
    covered = {props["KAFKA_TOPIC"] for props in streams.values()}
    missing = _declared_topics() - covered
    assert not missing, f"topics with no ksqlDB stream: {sorted(missing)}"


def test_no_stream_over_a_topic_nobody_creates() -> None:
    """The other direction: a stream over a topic create-topics.sh never makes.

    ksqlDB refuses to register it, so this would break `make up` rather than
    merely be useless.
    """
    streams = _source_streams()
    unknown = {props["KAFKA_TOPIC"] for props in streams.values()} - _declared_topics()
    assert not unknown, f"streams over topics not in topics.tsv: {sorted(unknown)}"


def test_source_streams_declare_only_a_key_column() -> None:
    """Value columns come from Schema Registry, never from a fourth copy.

    A column list here would be the schema written down a fourth time
    (after Avro, Postgres and ClickHouse) and would rot the first time a
    field is added.
    """
    for name, props in _source_streams().items():
        columns = props["_columns"]
        assert columns.upper().endswith("KEY"), (
            f"{name} declares value columns ({columns!r}); "
            "only the KEY column belongs here"
        )
        assert columns.count(",") == 0, f"{name} declares more than one column: {columns!r}"


def test_key_column_never_collides_with_a_value_field() -> None:
    """ksqlDB refuses a schema whose key and value share a column name."""
    for name, props in _source_streams().items():
        key_column = props["_columns"].split()[0].lower()
        fields = _fields(props["KAFKA_TOPIC"])
        assert key_column not in fields, (
            f"{name} key column {key_column!r} also exists in "
            f"{props['KAFKA_TOPIC']}.avsc - ksqlDB rejects the duplicate"
        )


def test_every_stream_is_avro_and_timestamped_on_a_real_field() -> None:
    """Row time must be the event's own time, from a field that exists.

    TIMESTAMP naming a field the schema does not have is accepted at
    CREATE and then fails every query, which is a long way from the cause.
    """
    for name, props in _source_streams().items():
        assert props["VALUE_FORMAT"] == "AVRO", f"{name} is not AVRO"
        stamp = props.get("TIMESTAMP")
        assert stamp, f"{name} sets no TIMESTAMP; row time would be broker arrival time"
        fields = _fields(props["KAFKA_TOPIC"])
        assert stamp in fields, (
            f"{name} timestamps on {stamp!r}, absent from {props['KAFKA_TOPIC']}.avsc"
        )


def test_derived_tables_state_their_replication() -> None:
    """A one-replica sink topic inside a three-replica cluster is invisible.

    ksqlDB does not inherit the broker's default.replication.factor for a
    sink topic - measured, not assumed - so every CREATE TABLE ... AS here
    has to say the number.
    """
    text = (KSQL_DIR / "020_live_views.sql").read_text()
    creates = re.findall(r"(?is)CREATE\s+TABLE[^;]*?\)\s*AS", text)
    assert creates, "020_live_views.sql declares no table"
    for create in creates:
        assert re.search(r"(?i)REPLICAS\s*=\s*3", create), (
            "a CREATE TABLE ... AS does not set REPLICAS = 3"
        )


def test_check_regex_matches_what_the_ddl_declares() -> None:
    """The checker must not pass by finding nothing.

    check-ksql.py fails when a declared object is missing. If its regex
    stopped matching the DDL it would declare nothing, find nothing missing,
    and report OK against an empty server - the same vacuous pass Q0 exists
    to stop in the quality bars.
    """
    found = DECLARED.findall(_sql_text())
    names = {name.lower() for _, name in found}
    kinds = {kind.upper() for kind, _ in found}
    assert len(names) >= len(_declared_topics()) + 1, (
        f"the checker's expression found only {sorted(names)}"
    )
    assert kinds == {"STREAM", "TABLE"}, f"expected both kinds, found {kinds}"

    checker = (KSQL_DIR / "check-ksql.py").read_text()
    assert DECLARED.pattern in checker, (
        "check-ksql.py no longer uses the expression this test exercises"
    )


def test_every_topic_has_a_schema_to_register() -> None:
    """A stream infers its columns from the Registry, so the .avsc must exist.

    ksqlDB refuses CREATE STREAM with no column list when the subject is
    absent - "Schema for message values on topic X does not exist" - and
    that aborts the bring-up before bootstrap. A topic added to topics.tsv
    without its schema fails here instead, where it costs nothing.
    """
    missing = {
        topic for topic in _declared_topics()
        if not (SCHEMA_DIR / f"{topic}.avsc").is_file()
    }
    assert not missing, f"topics with no .avsc to register: {sorted(missing)}"


def test_schemas_are_registered_before_the_streams_are_created() -> None:
    """Order, not just presence.

    The producers register their own schemas, but they start at bootstrap -
    after ksql-init. On a stack that has just been destroyed the Registry is
    empty at that moment, so the schemas have to be put there first. This is
    the order that failed on the server once.
    """
    root = (NUS / "Makefile").read_text()
    order = [
        root.index("--no-print-directory topics"),
        root.index("--no-print-directory schemas"),
        root.index("--no-print-directory ksql-ddl"),
    ]
    assert order == sorted(order), (
        "make up must run topics, then schemas, then ksql-ddl"
    )

    kafka_make = (NUS / "c-infra-kafka" / "Makefile").read_text()
    assert "run --rm schema-init" in kafka_make, "schemas does not run the one-shot"


def test_the_registered_subject_matches_what_the_producers_use() -> None:
    """Subject naming has to agree in two places or nothing lines up.

    register-schemas.py writes <topic>-value. The producers reach the same
    name through Confluent's default TopicNameStrategy, because
    nus_common.kafka serializes with a SerializationContext of (topic,
    VALUE). If either side changed, ksqlDB would read one subject while the
    producers wrote another, and the mismatch would look like a schema
    problem rather than a naming one.
    """
    registrar = (KSQL_DIR.parent / "schemas" / "register-schemas.py").read_text()
    assert 'f"{path.stem}-value"' in registrar, (
        "register-schemas.py no longer names subjects <topic>-value"
    )

    producer = (NUS / "z-lib" / "nus-common" / "nus_common" / "kafka.py").read_text()
    assert "MessageField.VALUE" in producer, (
        "the producer no longer serializes with a VALUE SerializationContext, "
        "so the subject it writes may not be <topic>-value any more"
    )


def test_ksql_is_wired_into_bring_up_and_verification() -> None:
    """Registered by `make up`, and checked by a target that can fail."""
    root = (NUS / "Makefile").read_text()
    assert "--no-print-directory ksql-ddl" in root, "make up does not run ksql-ddl"
    assert re.search(r"^verify:.*\bverify-ksqldb\b", root, re.M), (
        "make verify does not include verify-ksqldb"
    )

    kafka_make = (NUS / "c-infra-kafka" / "Makefile").read_text()
    assert "run --rm ksql-init" in kafka_make, "ksql-ddl does not run the one-shot"
    assert "/ksql/check-ksql.py" in kafka_make, (
        "verify-ksqldb still prints SHOW STREAMS instead of failing on an empty server"
    )
