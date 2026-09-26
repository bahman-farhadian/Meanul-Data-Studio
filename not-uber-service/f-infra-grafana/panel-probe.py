#!/usr/bin/env python3
"""Emit SQL that runs every provisioned panel's query against the warehouse.

check-dashboards.py proves a panel's SQL is well-formed and reads the right
tables. It cannot prove the SQL RUNS: a renamed column, a function that does
not exist on this ClickHouse version, an alias ClickHouse resolves before the
column it shadows - each of those is a panel that renders an error to
whoever opens it first, and nothing in the repository would have said so.

This is the Grafana counterpart of g-infra-superset/init/verify-assets.py,
which executes every Superset metric for the same reason. It writes a SQL
script to stdout; the Makefile pipes it into clickhouse-client, which stops
at the first error - so the last panel name printed is the one that broke.

WHAT IT DOES NOT CLAIM. This proves the query is valid and how many rows it
returns. It does not prove the panel RENDERS: a wrong field name in a
fieldConfig override, or a viz option this Grafana version does not know,
still needs eyes on the dashboard.

Grafana's macros are expanded to a concrete window rather than mocked. The
window is deliberately wide - seven days - because the dashboards range from
now-15m to now-7d and a panel reading a daily rollup has nothing to say
inside fifteen minutes.

A panel that filters on a template variable gets the variable's own query
substituted as a scalar subquery, so it is asked about a value that really
exists rather than a placeholder that never will.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "provisioning" / "dashboards" / "json"

WINDOW = "INTERVAL 7 DAY"
MACROS = [
    (re.compile(r"\$__timeFilter\(\s*([a-z_][a-z0-9_]*)\s*\)", re.I),
     lambda m: f"{m.group(1)} >= now() - {WINDOW}"),
    (re.compile(r"\$__timeInterval\(\s*([a-z_][a-z0-9_]*)\s*\)", re.I),
     lambda m: f"toStartOfInterval({m.group(1)}, INTERVAL 1 MINUTE)"),
    (re.compile(r"\$__fromTime\b"), lambda m: f"(now() - {WINDOW})"),
    (re.compile(r"\$__toTime\b"), lambda m: "now()"),
]


def variables(dash: dict) -> dict[str, str]:
    """Each template variable's own query, ready to use as a scalar."""
    found = {}
    for var in (dash.get("templating") or {}).get("list") or []:
        query = var.get("query")
        if isinstance(query, dict):
            query = query.get("rawSql")
        if query and var.get("name"):
            found[var["name"]] = str(query).strip().rstrip(";")
    return found


def expand(sql: str, vars_: dict[str, str]) -> str:
    for pattern, replace in MACROS:
        sql = pattern.sub(replace, sql)
    # `col = '${var}'` becomes `col IN (the variable's own query)`, not a
    # scalar subquery. Two reasons, both learned by trying the other way:
    # a scalar subquery of a non-Nullable type RAISES when it comes back
    # empty (code 125) rather than matching nothing, which would turn "this
    # variable has no recent value" into a failed check; and the variable's
    # query already ends in ORDER BY ... LIMIT n, so it cannot simply be
    # suffixed. IN over an empty set is quiet and correct.
    for name, query in vars_.items():
        sql = re.sub(
            r"=\s*'\$\{%s\}'" % re.escape(name),
            lambda _m, q=query: f"IN ({q})",
            sql,
        )
    return sql


def literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


# A real panel filters on a literal - WHERE zone_id = '142' - and touches one
# Distributed table. Substituting the template variable turns that into
# `zone_id IN (SELECT ... FROM another Distributed table)`, and ClickHouse
# refuses a Distributed table inside a subquery of a Distributed query:
# DISTRIBUTED_IN_JOIN_SUBQUERY_DENIED, code 288, and only ever on two shards
# or more. It is an artefact of the probe, not of the dashboard.
#
# 'global' rather than 'local': local is only correct when both tables shard
# on the same key, and they do not here - rider_positions shards on rider_id
# while the variable reads trip_events sharded on trip_id, so local would
# quietly drop rows. global materializes the subquery once and broadcasts
# it, which costs a pass and is right whatever the sharding.
SUBQUERY_SETTINGS = " SETTINGS distributed_product_mode = 'global'"


def settings_for(expanded_sql: str) -> str:
    return SUBQUERY_SETTINGS if re.search(r"(?i)\bIN\s*\(\s*SELECT", expanded_sql) else ""


# A panel asking about the last few minutes may legitimately be quiet: there
# might be no trip in progress at this instant. A panel reading a rollup may
# not - every rollup here has thousands of rows, so zero means the query can
# never return anything, whatever it looks like. The rule is the panel's own
# window, not a list of exceptions to maintain.
NARROW = re.compile(r"(?i)\bINTERVAL\s+\d+\s+(MINUTE|HOUR)\b")


def may_be_quiet(raw_sql: str) -> bool:
    return bool(NARROW.search(raw_sql)) or "${" in raw_sql


def main() -> int:
    files = sorted(JSON_DIR.glob("*.json"))
    if not files:
        print(f"-- no dashboards in {JSON_DIR}", file=sys.stderr)
        return 1

    lines = [
        "SELECT '=== every provisioned panel, run against this warehouse ===' AS section FORMAT TSVRaw;"
    ]
    probed = 0
    for path in files:
        dash = json.loads(path.read_text())
        uid = dash.get("uid") or path.stem
        vars_ = variables(dash)
        for panel in dash.get("panels") or []:
            for target in panel.get("targets") or []:
                sql = (target.get("rawSql") or "").strip().rstrip(";")
                if not sql:
                    continue
                probed += 1
                quiet = may_be_quiet(target.get("rawSql") or "")
                marker = "?" if quiet else " "
                label = f"{uid:<16} {panel.get('id'):>2}{marker} {panel.get('title')}"
                verdict = "'quiet'" if quiet else "'FAIL - returns nothing'"
                expanded = expand(sql, vars_)
                lines.append(f"SELECT {literal(label)} AS panel FORMAT TSVRaw;")
                lines.append(
                    "SELECT concat('     ', if(count() > 0, 'ok', " + verdict + "),\n"
                    "              '  rows ', toString(count())) FROM (\n"
                    + expanded
                    + "\n)" + settings_for(expanded) + " FORMAT TSVRaw;"
                )
    if not probed:
        print("-- no panel carried a query", file=sys.stderr)
        return 1

    lines.append(
        f"SELECT '=== {probed} panels ran without error ===' AS section FORMAT TSVRaw;"
    )
    lines.append(
        "SELECT '    A panel marked ? asks about the last few minutes, or filters "
        "on a template variable. Those may be quiet; the rest read rollups and "
        "may not.' AS note FORMAT TSVRaw;"
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
