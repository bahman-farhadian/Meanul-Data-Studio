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
  `docker compose run --rm` against the component's compose file — they do
  their job and remove themselves.
- **Everything else comes up through the root compose file only** —
  never `up` a component's own compose file when assembling the stack
  (volumes carry fixed `nus-*` names, so the one-shot output is shared
  either way).

## 0. One-time groundwork

```bash
docker network create nus-backbone   # shared network ("nus" = not-uber-service)
cp .env.example .env                 # stack-wide settings (lb-a/lb-b entry tier)
```

## 1. Piece a — a-infra-postgres (+ lb-a/lb-b entry tier)

```bash
# settings — EDIT THE PASSWORDS
cp a-infra-postgres/.env.example a-infra-postgres/.env

# one-shot: generate the nus-etcd TLS certificates (removes itself on exit)
docker compose -f a-infra-postgres/docker-compose.yaml run --rm etcd-certgen

# bring everything assembled so far up — ALWAYS via the root compose file
docker compose up -d --build
```

Verify it: [a-infra-postgres/README.md](a-infra-postgres/README.md)
(etcd health, `patronictl list`, connecting as a DBA, reading the HAProxy
stats page at <http://localhost:8404/stats>).

**Post-bootstrap (once, after the first successful start):** flip the etcd
cluster state from `new` to `existing`:

```bash
vim a-infra-postgres/etcd.env
# in vim:  :%s/^ETCD_INITIAL_CLUSTER_STATE=new/ETCD_INITIAL_CLUSTER_STATE=existing/
# then save and quit with  :wq

docker compose up -d    # recreates only the etcd containers; data persists
```

Why this flip matters (split-brain protection when a volume is ever lost):
[a-infra-postgres/README.md](a-infra-postgres/README.md#etcd-cluster-lifecycle--new-vs-existing).

## 2. Next pieces — b ... n

Added here as each component lands, in the same shape as piece a: copy its
`.env.example` if it has one, run its one-shot containers (if any) with
`docker compose -f <component>/docker-compose.yaml run --rm <name>`,
activate its `include:` entry in the root [`docker-compose.yaml`](docker-compose.yaml),
run `docker compose up -d --build`, then its post-bootstrap commands (if
any) — verification always per the component README.

## Teardown

```bash
docker compose down              # whole stack, keep data volumes
docker compose down -v           # whole stack, destroy data volumes
docker network rm nus-backbone   # only if removing the stack for good
```

After a `-v` teardown, repeat each piece's one-shot and post-bootstrap
steps on the next bring-up (for piece a: regenerate certs, set
`ETCD_INITIAL_CLUSTER_STATE` back to `new` first, flip again after).
