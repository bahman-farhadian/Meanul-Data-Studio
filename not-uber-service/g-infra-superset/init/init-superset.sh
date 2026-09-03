#!/bin/bash
# Prepare Superset: create its own tables, create the admin user, load the
# built-in roles, and register the ClickHouse connection.
#
# Run as a one-shot:  docker compose run --rm superset-init
#
# Safe to run again. Every step either skips work that is already done or
# updates it in place, so this is also how the ClickHouse password is
# refreshed after it changes.
set -euo pipefail

echo "== creating or upgrading Superset's own tables =="
superset db upgrade

echo "== creating the admin user (skipped if it already exists) =="
superset fab create-admin \
    --username "${SUPERSET_ADMIN_USER}" \
    --firstname Admin \
    --lastname User \
    --email "${SUPERSET_ADMIN_EMAIL}" \
    --password "${SUPERSET_ADMIN_PASSWORD}" || echo "admin user already there, moving on"

echo "== loading the built-in roles and permissions =="
superset init


# The datasets, charts and dashboard live in assets/ as plain YAML, the same
# way Grafana's dashboards do. --overwrite makes this the way an edited chart
# is rolled out: change the file, run the one-shot again. A chart edited in the
# browser is NOT written back to these files - export it and commit it.
echo "== importing the datasets, charts and dashboard =="
# Before the credentials, deliberately. The bundle carries a database file so
# the datasets have a uuid to attach to, and its uri has no password in it --
# a password in a committed file is not an option. --overwrite applies that
# passwordless uri to the connection, so whatever ran before it loses its
# password. Registering afterwards is what makes the credential survive.
superset import-directory /app/assets --overwrite

echo "== registering the database connections =="
python /init/register_database.py

echo
echo "Superset is ready. Log in as ${SUPERSET_ADMIN_USER}."
