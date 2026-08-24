"""Hand the Debezium connector definition to Kafka Connect.

Run as a one-shot:  docker compose run --rm connector-register

The database connection details are injected from the environment, so the
committed JSON never holds a password. The script uses a PUT, which creates
the connector the first time and replaces its configuration on every later
run - so after editing nus-pg.json, running this again is all it takes.

Only the standard library is used, so the one-shot needs a plain Python
image and no dependency install.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

CONNECT_URL = os.environ.get("CONNECT_URL", "http://debezium-connect:8083")
CONNECTOR_FILE = os.environ.get("CONNECTOR_FILE", "/nus-pg.json")

# How long to wait for Connect to answer. Connect loads its plugins first,
# which takes a while on a cold start.
WAIT_ATTEMPTS = 30
WAIT_SECONDS = 5


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, str]:
    """Send one request to the Connect REST API and return status and body."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{CONNECT_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as err:
        # An HTTP error is still an answer: return it so the caller can show
        # Connect's own explanation instead of a bare stack trace.
        return err.code, err.read().decode()


def wait_for_connect() -> None:
    """Block until the Connect REST API answers, or give up and fail."""
    print(f"waiting for Kafka Connect at {CONNECT_URL} ...")
    for attempt in range(1, WAIT_ATTEMPTS + 1):
        try:
            status, _ = request("GET", "/")
            if status == 200:
                print("Kafka Connect is answering")
                return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        print(f"  not ready yet (attempt {attempt}/{WAIT_ATTEMPTS})")
        time.sleep(WAIT_SECONDS)
    sys.exit("Kafka Connect never answered - is debezium-connect running?")


def build_config() -> tuple[str, dict]:
    """Read the connector file and fill in the connection details."""
    with open(CONNECTOR_FILE) as handle:
        definition = json.load(handle)

    name = definition["name"]
    config = definition["config"]
    config["database.hostname"] = os.environ["PG_HOST"]
    config["database.port"] = os.environ["PG_PORT"]
    config["database.dbname"] = os.environ["PG_DATABASE"]
    config["database.user"] = os.environ["PG_USER"]
    config["database.password"] = os.environ["PG_PASSWORD"]
    return name, config


def main() -> None:
    wait_for_connect()
    name, config = build_config()

    print(f"registering connector '{name}' against {config['database.hostname']}")
    status, body = request("PUT", f"/connectors/{name}/config", config)
    if status not in (200, 201):
        # The most common cause is a table in table.include.list that does not
        # exist yet, which means h-bootstrap has not run.
        sys.exit(f"Connect refused the configuration (HTTP {status}):\n{body}")

    # Give the connector a moment to actually start before reporting on it.
    time.sleep(3)
    status, body = request("GET", f"/connectors/{name}/status")
    print(f"status (HTTP {status}):")
    print(body)

    state = json.loads(body).get("connector", {}).get("state") if status == 200 else None
    if state != "RUNNING":
        print(
            "\nThe connector is registered but not RUNNING yet. That can be "
            "normal right after start; check again with:\n"
            f"  curl -s {CONNECT_URL}/connectors/{name}/status"
        )


if __name__ == "__main__":
    main()
