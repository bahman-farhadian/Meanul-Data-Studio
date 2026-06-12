# not-uber-service — stack runbook

How to bring the full stack up with the root
[`docker-compose.yaml`](docker-compose.yaml), piece by piece, in the
alphabetic build order (`a-` ... `n-`). **This file covers assembly order
only** — what each component is, how to verify it, and how to operate it
lives in that component's own README. Architecture: the repository's main
[README](../README.md).

All commands run from this directory (`not-uber-service/`).

## 0. One-time groundwork

```bash
docker network create nus-backbone   # shared network ("nus" = not-uber-service)
cp .env.example .env                 # stack-wide settings (lb-a/lb-b entry tier)
```

## 1. Assemble a piece

Every component follows the same four steps — one-shots always run via
`docker compose run --rm` (they remove themselves) and are never part of
`up`; volumes carry fixed `nus-*` names, so standalone `-f` runs and the
root stack share the same data:

```bash
cp <component>/.env.example <component>/.env                            # 1. settings (edit secrets!)
docker compose -f <component>/docker-compose.yaml run --rm <one-shot>   # 2. one-shots, if any
docker compose -f <component>/docker-compose.yaml up -d --build         # 3. verify standalone
# 4. make sure its include: entry is active in ./docker-compose.yaml
```

Then follow the **component README** for its verification and any
post-bootstrap steps.

| Piece | One-shot(s) | Post-bootstrap | Details |
| --- | --- | --- | --- |
| `a-infra-postgres` | `etcd-certgen` | flip etcd to `existing` | [README](a-infra-postgres/README.md) |
| `b-` ... `n-` | _added as each component lands_ | | |

## 2. Run the whole stack

The root compose owns the lb-a/lb-b entry tier and `include:`s every
landed component — once a piece is assembled, this is the single
"everything up" command:

```bash
docker compose up -d
```

Entry points (HAProxy stats: <http://localhost:8404/stats>) and DBA access
are documented in
[a-infra-postgres/README.md](a-infra-postgres/README.md#connecting-as-a-dba).

## Teardown

```bash
docker compose down              # whole stack, keep data volumes
docker compose down -v           # whole stack, destroy data volumes
docker network rm nus-backbone   # only if removing the stack for good
```

After a `-v` teardown, each component's one-shot and post-bootstrap steps
must be repeated on the next bring-up (see the component READMEs).
