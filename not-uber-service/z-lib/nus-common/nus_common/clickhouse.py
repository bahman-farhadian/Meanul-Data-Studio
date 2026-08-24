"""Talking to ClickHouse through the entry tier.

One thing decides how this module is written: ClickHouse is fast at
inserting large batches and slow at inserting single rows. Every insert here
therefore takes a list of rows, and callers collect events until they have
enough (or until enough time has passed) before calling.
"""

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from nus_common import config
from nus_common.logging import get_logger

log = get_logger(__name__)

_client: Client | None = None


def client() -> Client:
    """The shared ClickHouse connection.

    It points at lb-a, not at a single node, so queries and inserts are
    spread over whichever nodes are healthy.
    """
    global _client
    if _client is None:
        host = config.optional("CH_HOST", "lb-a")
        port = config.integer("CH_HTTP_PORT", 8123)
        _client = clickhouse_connect.get_client(
            host=host,
            port=port,
            username=config.optional("CH_USER", "nus"),
            password=config.required("CH_PASSWORD"),
            database=config.optional("CH_DATABASE", "nus"),
            connect_timeout=10,
            send_receive_timeout=60,
        )
        log.info("clickhouse client ready", extra={"host": host, "port": port})
    return _client


def insert_rows(table: str, rows: list[list], column_names: list[str]) -> int:
    """Insert a batch of rows and return how many were sent.

    An empty batch is not an error; it just does nothing. Callers flush on a
    timer as well as on a row count, and most timer flushes are empty.
    """
    if not rows:
        return 0

    client().insert(table, rows, column_names=column_names)
    log.debug("inserted", extra={"table": table, "rows": len(rows)})
    return len(rows)


def ping() -> bool:
    """True when ClickHouse answers. Used by the waiting helpers."""
    return client().command("SELECT 1") == 1
