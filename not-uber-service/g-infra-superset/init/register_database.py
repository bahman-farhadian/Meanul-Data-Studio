"""Add (or refresh) the ClickHouse connection inside Superset.

Superset keeps its database connections as rows in its own small database,
not in a config file, so they cannot simply be provisioned the way Grafana's
data source is. This script does it once, from inside the application, and
can be run again at any time - it updates the existing entry instead of
creating a second one.

Run through the one-shot:  docker compose run --rm superset-init

Note for upgrades: this reaches into Superset's own model classes, which is
the part most likely to need a small change when Superset's major version
moves. Everything else in this component uses the stable command line.
"""

import os

from superset.app import create_app

# The name shown in the Superset user interface.
DATABASE_NAME = "ClickHouse (nus)"


def build_uri() -> str:
    """Build the connection string Superset will use for ClickHouse.

    It points at lb-a, the entry tier, and not at a single ClickHouse node,
    so queries are spread over whichever nodes are healthy.
    """
    user = os.environ.get("CH_USER", "nus")
    password = os.environ["CH_PASSWORD"]
    host = os.environ.get("CH_HOST", "lb-a")
    port = os.environ.get("CH_HTTP_PORT", "8123")
    database = os.environ.get("CH_DATABASE", "nus")
    # clickhousedb:// is the scheme registered by the clickhouse-connect
    # driver that the image installs.
    return f"clickhousedb://{user}:{password}@{host}:{port}/{database}"


def main() -> None:
    app = create_app()
    with app.app_context():
        from superset import db
        from superset.models.core import Database

        uri = build_uri()
        existing = db.session.query(Database).filter_by(database_name=DATABASE_NAME).one_or_none()

        if existing is None:
            print(f"creating connection '{DATABASE_NAME}'")
            existing = Database(database_name=DATABASE_NAME)
            db.session.add(existing)
        else:
            print(f"connection '{DATABASE_NAME}' already exists, refreshing it")

        existing.sqlalchemy_uri = uri
        # Charts only read. Writing from a dashboard tool is how an analytics
        # database quietly turns into a second source of truth.
        existing.allow_dml = False
        # Let people write their own SQL in SQL Lab; that is the main way to
        # explore this data before turning a query into a chart.
        existing.expose_in_sqllab = True
        existing.allow_ctas = False
        existing.allow_cvas = False

        db.session.commit()
        print("done")


if __name__ == "__main__":
    main()
