"""Logging that a machine can read.

Every record is printed as one JSON line on standard output. Docker collects
standard output, so there is nothing to configure and no log file to rotate.
JSON matters because these services produce a lot of lines: searching for
"every warning from dispatch about trip X" should be a filter, not a read.

Use it once at start:

    from nus_common.logging import setup_logging, get_logger
    setup_logging("dispatch-service")
    log = get_logger(__name__)
    log.info("trip assigned", extra={"trip_id": trip_id, "driver_id": driver_id})

Anything passed in `extra` becomes a field of its own in the JSON line.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

# Fields the logging library puts on every record. Anything not in this set
# was added by the caller through `extra`, and belongs in the output.
_STANDARD_FIELDS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """Turn a log record into a single JSON line."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            # Always UTC, like every other timestamp in the stack.
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)

        # default=str so a stray datetime or UUID cannot break logging itself.
        return json.dumps(payload, default=str)


def setup_logging(service: str) -> None:
    """Send every log line to standard output as JSON.

    The level comes from LOG_LEVEL, so a noisy problem can be investigated by
    restarting one container with LOG_LEVEL=DEBUG.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # These libraries are chatty at INFO and say nothing useful at that level.
    for noisy in ("kafka", "urllib3", "clickhouse_connect"):
        logging.getLogger(noisy).setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    """Return the logger a module should use."""
    return logging.getLogger(name)
