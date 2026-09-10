#!/bin/sh
# Renders haproxy.cfg.template into the shared config volume, filling in
# REDIS_PASSWORD and a Basic-auth header for ksqlDB's own healthcheck probe.
# Idempotent — safe to re-run any time either credential changes.
#
# Not sed: a plain `sed s/X/Y/` breaks the moment a value contains the
# delimiter character (a `/` in the password looks like the end of the sed
# command), and this project has already been bitten by exactly that class of
# bug once, in Superset's ClickHouse connection string. awk's index()/substr()
# does a literal replace — no regex, no delimiter, safe for any password
# content except whitespace (see the note in the template: the Redis
# tcp-check sends AUTH as a raw inline command, which splits on spaces
# regardless of how the config was rendered).
set -eu

: "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"
: "${KSQLDB_ADMIN_USER:?KSQLDB_ADMIN_USER must be set}"
: "${KSQLDB_ADMIN_PASSWORD:?KSQLDB_ADMIN_PASSWORD must be set}"

# -w 0: busybox base64 wraps at 76 columns by default, which would split the
# header value across multiple lines of a single-line HAProxy directive.
ksqldb_basic_auth=$(printf '%s:%s' "$KSQLDB_ADMIN_USER" "$KSQLDB_ADMIN_PASSWORD" | base64 -w 0)

awk -v redis_pw="$REDIS_PASSWORD" -v ksql_auth="$ksqldb_basic_auth" '
  function replace(line, ph, val,    i, out) {
    out = "";
    while ((i = index(line, ph)) > 0) {
      out = out substr(line, 1, i - 1) val;
      line = substr(line, i + length(ph));
    }
    return out line;
  }
  {
    line = $0;
    line = replace(line, "__REDIS_PASSWORD__", redis_pw);
    line = replace(line, "__KSQLDB_BASIC_AUTH__", ksql_auth);
    print line;
  }
' /template/haproxy.cfg.template > /out/haproxy.cfg

chmod 0444 /out/haproxy.cfg
echo "haproxy.cfg rendered"
