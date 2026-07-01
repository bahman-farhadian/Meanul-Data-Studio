# not-uber-service — stack runbook

How to bring the full stack up with the root
[`docker-compose.yaml`](docker-compose.yaml), piece by piece, in the
alphabetic build order (`a-` ... `n-`). **This file covers assembly order
only** — what each component is and how to verify it lives in that
component's own README. Architecture: the repository's main
[README](../README.md).

All commands run from this directory (`not-uber-service/`). Two rules
apply to every piece:

- **One-shot containers** (cert generation and the like) run via
  `docker compose run --rm <name>` from this directory, against the root
  compose file — they do their job and remove themselves. Running them
  against a component's own compose file works too, but the volumes they
  create get labelled with that component's project name, and every later
  root `up` warns `volume ... was created for project "a-infra-postgres"`
  (harmless, but noisy — keep one project scope and it never appears).
- **Everything else comes up through the root compose file only** —
  never `up` a component's own compose file when assembling the stack
  (volumes carry fixed `nus-*` names, so the one-shot output is shared
  either way).
- **Always apply the server resource profile** on `up`:
  [`compose.server.yaml`](compose.server.yaml). The stack targets a
  dedicated Docker server with 20 CPU cores and 96 GB RAM; the profile
  enforces the per-container CPU/memory limits and the no-swap policy from
  the main README, section 2.9. A plain `docker compose up` runs unlimited
  and is acceptable only for a quick functional check.

## 0. One-time groundwork

```bash
# shared network ("nus" = not-uber-service)
docker network create nus-backbone

# stack-wide settings (lb-a/lb-b entry tier)
cp .env.example .env
```

## 1. Piece a — a-infra-postgres (+ lb-a/lb-b entry tier)

```bash
# settings — EDIT THE PASSWORDS
cp a-infra-postgres/.env.example a-infra-postgres/.env

# one-shot: generate the nus-etcd TLS certificates (removes itself on exit)
docker compose run --rm etcd-certgen

# bring everything assembled so far up — ALWAYS via the root compose file,
# with the server resource profile
docker compose -f docker-compose.yaml -f compose.server.yaml up -d --build
```

Verify it: [a-infra-postgres/README.md](a-infra-postgres/README.md)
(etcd health, `patronictl list`, connecting as a DBA, reading the HAProxy
stats page at <http://localhost:8404/stats>).

**Post-bootstrap (once, after the first successful start):** flip the etcd
cluster state from `new` to `existing`:

```bash
# set ETCD_INITIAL_CLUSTER_STATE=existing
vim a-infra-postgres/etcd.env

# re-apply: recreates only the etcd containers; data persists
docker compose -f docker-compose.yaml -f compose.server.yaml up -d
```

This flip is **local operational state — never commit it**; the repository
keeps `new` so a fresh clone can bootstrap from empty volumes.

Why this flip matters (split-brain protection when a volume is ever lost):
[a-infra-postgres/README.md](a-infra-postgres/README.md#etcd-cluster-lifecycle--new-vs-existing).

## 2. Next pieces — b ... n

Added here as each component lands, in the same shape as piece a: copy its
`.env.example` if it has one, activate its `include:` entry in the root
[`docker-compose.yaml`](docker-compose.yaml), run its one-shot containers
(if any) with `docker compose run --rm <name>`,
add its limits to `compose.server.yaml`, run the profile-applied `up` from
piece a, then its post-bootstrap commands (if any) — verification always
per the component README.

## Teardown

```bash
# whole stack, keep data volumes
docker compose down

# whole stack, destroy data volumes
docker compose down -v

# only if removing the stack for good
docker network rm nus-backbone
```

After a `-v` teardown, repeat each piece's one-shot and post-bootstrap
steps on the next bring-up (for piece a: regenerate certs, set
`ETCD_INITIAL_CLUSTER_STATE` back to `new` first, flip again after).
