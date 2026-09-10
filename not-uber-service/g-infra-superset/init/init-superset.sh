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

# Datasets, charts and a dashboard were designed here previously - see git
# history for g-infra-superset/assets/ - but are deliberately not imported
# at this stage. This piece currently provisions Superset itself and its
# two database connections only; dashboard content depends on tables and
# data-generation logic (pieces h onward) not yet verified correct, so
# building against them now would mean redoing that work later.
echo "== registering the database connections =="
python /init/register_database.py

echo
echo "Superset is ready. Log in as ${SUPERSET_ADMIN_USER}."
