"""Superset settings for the not-uber-service stack.

Superset needs a small database of its own for users, saved charts and
dashboards. That is a different thing from the data it shows: the data lives
in ClickHouse, and this file only configures Superset itself.

SQLite is used for that small database because this stack has exactly one
dashboard user. It is a deliberate trade documented in the main README
(section 2.9): a second PostgreSQL cluster for Superset's own bookkeeping
would cost memory the analytics nodes need, and the OLTP cluster must stay
untouched by dashboards.

SQLite has one rule: only one process may write at a time. That is why
Superset runs with a single worker - see the command in docker-compose.yaml.
"""

import os

# --------------------------------------------------------------------------
# Superset's own database
# --------------------------------------------------------------------------
# Lives on a named volume, so saved dashboards survive a rebuild.
SQLALCHEMY_DATABASE_URI = "sqlite:////app/superset_home/superset.db"

# SQLite refuses to be used from another thread unless this is switched off.
SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False}}

# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------
# Signs session cookies. Changing it logs everybody out; losing it means
# saved database passwords can no longer be decrypted, so keep it in .env.
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
# In-process cache. With one worker and one user there is nothing to share
# between processes, so a separate cache service would only add a moving part.
CACHE_CONFIG = {
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
}
DATA_CACHE_CONFIG = CACHE_CONFIG

# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------
# How many rows a chart may pull back. ClickHouse can return millions; a
# browser cannot draw them.
ROW_LIMIT = 50_000
SUPERSET_WEBSERVER_TIMEOUT = 120

# Superset sits behind the HAProxy pair, which speaks plain HTTP inside the
# Docker network. Talisman would try to force HTTPS and break every redirect.
TALISMAN_ENABLED = False
ENABLE_PROXY_FIX = True

FEATURE_FLAGS = {
    # Lets a chart be built from a query written by hand, which is how most
    # exploration of this data starts.
    "ENABLE_TEMPLATE_PROCESSING": True,
    "DASHBOARD_RBAC": False,
}
