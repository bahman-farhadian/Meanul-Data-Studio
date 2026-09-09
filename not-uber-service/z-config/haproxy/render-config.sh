#!/bin/sh
# Renders haproxy.cfg.template into the shared config volume, filling in
# REDIS_PASSWORD. Idempotent — safe to re-run any time the password changes.
#
# Not sed: a plain `sed s/X/Y/` breaks the moment the password contains the
# delimiter character (a `/` in the password looks like the end of the sed
# command), and this project has already been bitten by exactly that class of
# bug once, in Superset's ClickHouse connection string. awk's index()/substr()
# does a literal replace — no regex, no delimiter, safe for any password
# content except whitespace (see the note in the template: the tcp-check
# sends AUTH as a raw inline command, which splits on spaces regardless of
# how the config was rendered).
set -eu

: "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"

awk -v pw="$REDIS_PASSWORD" '
  {
    line = $0; out = ""; ph = "__REDIS_PASSWORD__";
    while ((i = index(line, ph)) > 0) {
      out = out substr(line, 1, i - 1) pw;
      line = substr(line, i + length(ph));
    }
    print out line;
  }
' /template/haproxy.cfg.template > /out/haproxy.cfg

chmod 0444 /out/haproxy.cfg
echo "haproxy.cfg rendered"
