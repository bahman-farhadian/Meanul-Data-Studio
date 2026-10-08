"""Inventory the shipped schema so the dictionary cannot drift from it.

Sources are the bootstrap migrations, the ClickHouse DDL, and topics.tsv.
A live database is not required. If a migration and this inventory disagree,
the migration wins and the generated document is stale.
"""

from __future__ import annotations

import re
from pathlib import Path

NUS = Path(__file__).resolve().parents[1]
MIGRATIONS = NUS / "h-bootstrap" / "migrations"
CH_DDL = NUS / "e-infra-clickhouse" / "ddl"
TOPICS = NUS / "c-infra-kafka" / "topics" / "topics.tsv"
KSQL = NUS / "c-infra-kafka" / "ksql"
QUALITY = NUS / "z-config" / "quality.sql"
CHARTS = NUS / "g-infra-superset" / "assets" / "charts"
DATASETS = NUS / "g-infra-superset" / "assets" / "datasets"
DASHBOARDS = NUS / "g-infra-superset" / "assets" / "dashboards"

CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:nus\.)?([a-z_][a-z0-9_]*)"
    r"(?:\s+ON\s+CLUSTER\s+[a-z0-9_]+)?\s*\(",
    re.I,
)
# A Distributed table has no column list of its own. It is created
# `AS` the local table, and that is the name services query.
AS_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:nus\.)?([a-z_][a-z0-9_]*)"
    r"(?:\s+ON\s+CLUSTER\s+[a-z0-9_]+)?\s+AS\s+(?:nus\.)?([a-z_][a-z0-9_]*)",
    re.I,
)
ADD_COL = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:nus\.)?([a-z_][a-z0-9_]*)\s+"
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)\s+(.+?);",
    re.I | re.S,
)
DROP_COL = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:nus\.)?([a-z_][a-z0-9_]*)\s+"
    r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
    re.I,
)
DROP_TABLE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:nus\.)?([a-z_][a-z0-9_]*)",
    re.I,
)
KSQL_OBJECT = re.compile(
    r"CREATE\s+(STREAM|TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
    re.I,
)
REFERENCES = re.compile(r"REFERENCES\s+([a-z_][a-z0-9_]*)\s*\(", re.I)

F4 = (
    "Version 1 stores a payment as two columns on `trips`, "
    "`payment_method` and `driver_payout`. A failed or refunded charge "
    "cannot be represented. An append-only payments record is not in "
    "version 1."
)

SKIP_LINE = re.compile(
    r"^(CONSTRAINT|PRIMARY|UNIQUE|CHECK|FOREIGN|INDEX|KEY|ENGINE|PARTITION|"
    r"ORDER|TTL|SETTINGS|COMMENT)\b",
    re.I,
)


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _body(sql: str, open_at: int) -> str:
    depth = 0
    for i in range(open_at, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_at + 1 : i]
    return ""


def _columns(body: str) -> list[tuple[str, str]]:
    """Column definitions, with a parenthesised type kept as one column.

    Enum8 and numeric(10, 2) both contain commas. Splitting on every comma
    would record the enum symbols as columns.
    """
    cols: list[tuple[str, str]] = []
    depth = 0
    start = 0
    chunks: list[str] = []
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            chunks.append(body[start:i])
            start = i + 1
    chunks.append(body[start:])
    for chunk in chunks:
        line = " ".join(chunk.split())
        if not line or SKIP_LINE.match(line):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2 or not re.fullmatch(r"[a-z_][a-z0-9_]*", parts[0], re.I):
            continue
        cols.append((parts[0], parts[1]))
    return cols


def _apply(
    sql: str,
    tables: dict[str, list[tuple[str, str]]],
    aliases: dict[str, str] | None = None,
) -> None:
    sql = _strip_comments(sql)
    events: list[tuple[int, str, re.Match[str]]] = []
    for match in CREATE.finditer(sql):
        events.append((match.start(), "create", match))
    for match in AS_TABLE.finditer(sql):
        events.append((match.start(), "as", match))
    for _, kind, match in sorted(events, key=lambda item: item[0]):
        name = match.group(1)
        if name.endswith("_mv"):
            continue
        if kind == "create":
            tables[name] = _columns(_body(sql, match.end() - 1))
            continue
        source = match.group(2)
        if source not in tables:
            raise RuntimeError(f"{name} is AS {source}, which has no columns yet")
        tables[name] = list(tables[source])
        if aliases is not None:
            aliases[name] = source
    for match in ADD_COL.finditer(sql):
        table, column, typ = match.group(1), match.group(2), match.group(3)
        typ = " ".join(typ.split()).rstrip(",")
        cols = tables.setdefault(table, [])
        if column not in {existing for existing, _ in cols}:
            cols.append((column, typ))
    for match in DROP_COL.finditer(sql):
        table, column = match.group(1), match.group(2)
        if table in tables:
            tables[table] = [(n, t) for n, t in tables[table] if n != column]
    for match in DROP_TABLE.finditer(sql):
        tables.pop(match.group(1), None)
        if aliases is not None:
            aliases.pop(match.group(1), None)


def oltp_tables() -> dict[str, list[tuple[str, str]]]:
    tables: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        _apply(path.read_text(), tables)
    return tables


def oltp_edges() -> list[tuple[str, str, str, bool]]:
    """Child table, parent table, child column, whether the FK is NOT NULL."""
    edges = []
    for table, cols in oltp_tables().items():
        for column, typ in cols:
            match = REFERENCES.search(typ)
            if match:
                edges.append((table, match.group(1), column, "NOT NULL" in typ))
    return edges


def _warehouse() -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    tables: dict[str, list[tuple[str, str]]] = {}
    aliases: dict[str, str] = {}
    for path in sorted(CH_DDL.glob("*.sql")):
        _apply(path.read_text(), tables, aliases)
    return tables, aliases


def warehouse_tables() -> dict[str, list[tuple[str, str]]]:
    tables, _ = _warehouse()
    return tables


def warehouse_aliases() -> dict[str, str]:
    _, aliases = _warehouse()
    return aliases


def topics() -> list[tuple[str, str, str, str, str]]:
    rows = []
    for line in TOPICS.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        rows.append((parts[0], parts[1], parts[2], parts[3], parts[4]))
    return rows


def ksql_objects() -> list[tuple[str, str]]:
    found = []
    for path in sorted(KSQL.glob("*.sql")):
        text = _strip_comments(path.read_text())
        for kind, name in KSQL_OBJECT.findall(text):
            found.append((kind.upper(), name))
    return found


def quality_bars() -> list[tuple[str, str]]:
    """(bar name, pass line) as quality.sql prints them.

    The pass line is the last quoted string on the SELECT. measured is 0
    on a pass for every bar, including Q0, whose pass line is
    'empty tables = 0'.
    """
    bars = []
    for line in QUALITY.read_text().splitlines():
        if not re.search(r"SELECT\s+'Q\d+", line):
            continue
        name = re.search(r"'((?:Q\d+\s+)[^']+)'", line)
        quotes = re.findall(r"'([^']+)'", line)
        if name and len(quotes) >= 2:
            bars.append((name.group(1), quotes[-1]))
    return bars


def chart_count() -> int:
    return len(list(CHARTS.glob("*.yaml")))


def dataset_count() -> int:
    return len(list(DATASETS.rglob("*.yaml")))


def dashboard_count() -> int:
    return len(list(DASHBOARDS.glob("*.yaml")))


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|")


def render_dictionary() -> str:
    lines = [
        "# Data dictionary — version 1",
        "",
        "Generated from the shipped migrations, ClickHouse DDL, and",
        "`c-infra-kafka/topics/topics.tsv`. A live database is not consulted.",
        "If a source and this file disagree, the source wins. Regenerate",
        "with `python3 docs/schema_inventory.py` from `not-uber-service/`.",
        "",
        "`ways` and `ways_vertices_pgr` are built by",
        "`h-bootstrap/lion-prepare/build-graph.sql`, not by a migration,",
        "so they are not listed here.",
        "",
        F4,
        "",
        "## OLTP",
        "",
        "Produced by `h-bootstrap` and the live services. Consumed by the",
        "services, Debezium, and the archiver.",
        "",
    ]
    for table, cols in oltp_tables().items():
        lines.append(f"### `{table}`")
        lines.append("")
        lines.append("| Column | Type |")
        lines.append("| --- | --- |")
        for name, typ in cols:
            lines.append(f"| `{name}` | {_md_cell(typ)} |")
        lines.append("")
    lines += [
        "## Kafka topics",
        "",
        "Produced by the named service. Consumed by ksqlDB and",
        "`n-service-clickhouse-sink`. `cdc.*` topics are created by Debezium",
        "and are not in `topics.tsv`.",
        "",
        "| Topic | Partitions | Retention hours | Key | Purpose |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, partitions, retention, key, purpose in topics():
        lines.append(
            f"| `{name}` | {partitions} | {retention} | {key} | {_md_cell(purpose)} |"
        )
    aliases = warehouse_aliases()
    lines += [
        "",
        "## Warehouse",
        "",
        "A name ending in `_local` is the table on each node. The name",
        "without that suffix is the Distributed table services and",
        "dashboards query. Both are listed. A materialized view is a",
        "trigger, not a table, and is not listed.",
        "",
    ]
    for table, cols in warehouse_tables().items():
        lines.append(f"### `nus.{table}`")
        lines.append("")
        source = aliases.get(table)
        if source:
            lines.append(
                f"Distributed table. Same columns as `nus.{source}`. "
                "This is the name a query uses."
            )
            lines.append("")
        lines.append("| Column | Type |")
        lines.append("| --- | --- |")
        for name, typ in cols:
            lines.append(f"| `{name}` | {_md_cell(typ)} |")
        lines.append("")
    return "\n".join(lines)


def render_erd() -> str:
    tables = list(oltp_tables())
    lines = [
        "# OLTP entity diagram — version 1",
        "",
        "Tables are the ones left after `h-bootstrap/migrations` run in order.",
        "Edges are the foreign keys those migrations declare. A column with",
        "no `REFERENCES` is not drawn, even when the name looks like one.",
        "",
        "```mermaid",
        "erDiagram",
    ]
    for table in tables:
        lines.append(f"    {table}")
    for child, parent, column, required in oltp_edges():
        card = "||--|{" if required else "||--o{"
        lines.append(f"    {parent} {card} {child} : {column}")
    lines += ["```", ""]
    return "\n".join(lines)


def write_docs() -> None:
    docs = Path(__file__).resolve().parent
    (docs / "DATA_DICTIONARY.md").write_text(render_dictionary())
    (docs / "ERD.md").write_text(render_erd())


if __name__ == "__main__":
    write_docs()
    print(f"oltp {len(oltp_tables())} warehouse {len(warehouse_tables())}")
    print(f"bars {len(quality_bars())} ksql {len(ksql_objects())}")
    print(
        f"charts {chart_count()} datasets {dataset_count()} "
        f"dashboards {dashboard_count()}"
    )
