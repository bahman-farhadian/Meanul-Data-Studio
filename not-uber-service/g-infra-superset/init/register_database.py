"""Add (or refresh) the database connections inside Superset.

Superset keeps its connections as rows in its own small database, not in a
config file, so they cannot simply be provisioned the way Grafana's data
source is. This script does it from inside the application, and can be run
again at any time - it updates the existing entries rather than creating
second copies.

Run through the one-shot:  docker compose run --rm superset-init

Two connections are registered:

  ClickHouse (nus)              the warehouse every chart reads
  PostgreSQL (nus, read-only)   the OLTP source, for exploration only

The PostgreSQL one points at port 5433, the REPLICA pool, never the leader,
and refuses DML. Dashboards read the warehouse; this is here so the road
network, the zones and the traffic factors can be looked at in SQL Lab
without a query from a browser reaching the database the platform writes to.

The UUIDs are derived from the names rather than invented, so they are the
same on every deployment. The chart and dataset definitions under assets/
reference the ClickHouse one by that value, which is the only reason they can
be imported without a human clicking anything.

Note for upgrades: this reaches into Superset's own model classes, which is
the part most likely to need a small change when Superset's major version
moves. Everything else in this component uses the stable command line.
"""

import os
import uuid

from superset.app import create_app

# Namespace for every identifier this stack pins. Deriving them means the
# value in assets/databases/*.yaml and the value written here cannot drift.
NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/bahman-farhadian/Meanul-Data-Studio"
)

CLICKHOUSE_NAME = "ClickHouse (nus)"
CLICKHOUSE_UUID = uuid.uuid5(NAMESPACE, "database/clickhouse")
POSTGRES_NAME = "PostgreSQL (nus, read-only)"
POSTGRES_UUID = uuid.uuid5(NAMESPACE, "database/postgres")


def clickhouse_uri() -> str:
    """The warehouse, through the entry tier rather than one node."""
    user = os.environ.get("CH_USER", "nus")
    password = os.environ["CH_PASSWORD"]
    host = os.environ.get("CH_HOST", "lb-a")
    port = os.environ.get("CH_HTTP_PORT", "8123")
    database = os.environ.get("CH_DATABASE", "nus")
    return f"clickhousedb://{user}:{password}@{host}:{port}/{database}"


def postgres_uri() -> str:
    """The OLTP source, through the READ port.

    5433 is the replica pool on the entry tier. A dashboard tool has no
    business on the leader, and pointing at the replicas means an expensive
    exploratory query cannot slow down the trips being written.
    """
    user = os.environ.get("PG_USER", "postgres")
    password = os.environ["PG_PASSWORD"]
    host = os.environ.get("PG_HOST", "lb-a")
    port = os.environ.get("PG_READ_PORT", "5433")
    database = os.environ.get("PG_DATABASE", "postgres")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


def upsert(db, Database, name: str, identifier: uuid.UUID, uri: str) -> None:
    """Create the connection, or point an existing one at the same place."""
    existing = db.session.query(Database).filter_by(database_name=name).one_or_none()

    if existing is None:
        print(f"creating connection '{name}'")
        existing = Database(database_name=name)
        db.session.add(existing)
    else:
        print(f"connection '{name}' already exists, refreshing it")

    existing.uuid = identifier
    existing.sqlalchemy_uri = uri
    # Charts only read. Writing from a dashboard tool is how an analytics
    # database quietly turns into a second source of truth.
    existing.allow_dml = False
    # Let people write their own SQL in SQL Lab; that is the main way to
    # explore this data before turning a query into a chart.
    existing.expose_in_sqllab = True
    existing.allow_ctas = False
    existing.allow_cvas = False


def main() -> None:
    app = create_app()
    with app.app_context():
        from superset import db
        from superset.models.core import Database

        upsert(db, Database, CLICKHOUSE_NAME, CLICKHOUSE_UUID, clickhouse_uri())
        upsert(db, Database, POSTGRES_NAME, POSTGRES_UUID, postgres_uri())

        db.session.commit()
        print(f"done: {CLICKHOUSE_NAME} [{CLICKHOUSE_UUID}]")
        print(f"      {POSTGRES_NAME} [{POSTGRES_UUID}]")


if __name__ == "__main__":
    main()
