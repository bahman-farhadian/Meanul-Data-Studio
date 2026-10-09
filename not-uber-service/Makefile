# =============================================================================
# not-uber-service — stack control
# =============================================================================
# Everything runs from this directory. `make` prints the commands you run.
#
# This file is the runbook made executable. Several steps only work once
# something else has happened, and `make up` puts them in that order. Those
# steps stay as targets so a failed run can resume at the step that failed.
#
# Pieces a-h each have a Makefile because they have a check or a one-shot.
# The services do not: make lag, make errors, and make profile cover them.
#
# Full explanation of each piece: README.md. Architecture: ../README.md
#
# The long checks live in mk/. This file is the order: what `make up`,
# `make prepare`, `make verify`, and `make destroy` call.
# =============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE  := docker compose
# Required for RUN --mount=type=cache in the Python Dockerfiles. Compose v2
# uses BuildKit by default; this makes the contract explicit so a host that
# has DOCKER_BUILDKIT=0 in its environment cannot silently drop the cache.
export DOCKER_BUILDKIT := 1
export COMPOSE_DOCKER_CLI_BUILD := 1
NETWORK  := nus-backbone
ENV_FILE := .env
EXAMPLE  := .env.example
ETCD_ENV := a-infra-postgres/etcd.env

# Pieces that have their own checks or one-shots. Order follows the build
# order. The services (i-o) have no Makefile of their own.
include a-infra-postgres/Makefile
include b-infra-redis/Makefile
include c-infra-kafka/Makefile
include d-infra-debezium/Makefile
include e-infra-clickhouse/Makefile
include f-infra-grafana/Makefile
include g-infra-superset/Makefile
include h-bootstrap/Makefile

# How long to wait for a group of containers to report healthy. ClickHouse and
# Superset have a 40-60s start_period of their own, so this is generous.
WAIT_TIMEOUT ?= 420

# How long `make wait-live` will wait for driver-service to put the fleet on
# the road, and how long it then lets live trips run before the checks look.
#
# Measured at the step-12 10% rung (10,600 drivers): driver-service took 17
# minutes to reach its first tick - 5m waiting for cache-updater, 4m30s
# building road point pools, then 7m39s computing one pgRouting path per
# online driver. Nothing reaches driver_location in that window, so a
# `make verify` run inside it reports a stack that is merely starting as a
# stack that is broken. That is exactly what happened.
#
# WAIT_LIVE_SETTLE is about the checks, not the service: verify-positions
# reads trips that ENDED in the last hour and a trip takes about nine
# minutes, so the fleet being online is not the same as there being
# anything to measure.
WAIT_LIVE_TIMEOUT ?= 3600
WAIT_LIVE_SETTLE  ?= 900

# How far back `make errors` reads each container's logs. Every service logs
# something transient in its first few seconds (a client probing before it is
# ready, a watch reconnecting while a cluster forms) and a container that
# never restarts carries that moment for its whole life — a fixed line count
# never ages it out. A short, recent window does: it still catches anything
# actively wrong, and a one-time startup line falls out of it on its own.
ERRORS_WINDOW ?= 15m

# --- the stack, in the pieces the runbook names -------------------------------
ENTRY    := lb-a lb-b
PIECE_A  := etcd-1 etcd-2 etcd-3 pg-1 pg-2 pg-3
PIECE_B  := redis-1
PIECE_C  := kafka-1 schema-registry ksqldb-server
PIECE_D  := debezium-connect
PIECE_E  := ch-keeper-1 ch-keeper-2 ch-keeper-3 ch-s1r1 ch-s1r2 ch-s2r1 ch-s2r2
# OSM raster lives here, not as a later sidecar. HAProxy /tiles/ 503s
# ("no server available") if this list is only grafana.
PIECE_F  := grafana tiles
PIECE_G  := superset
SERVICES := cache-updater driver-service passenger-service dispatch-service city-service clickhouse-sink archiver-service
INFRA    := $(ENTRY) $(PIECE_A) $(PIECE_B) $(PIECE_C) $(PIECE_D) $(PIECE_E) $(PIECE_F) $(PIECE_G)
# Everything the bring-up waits on. Debezium Connect is started with the rest
# but deliberately not waited for: it takes minutes to scan its plugins, and
# nothing needs it until `cdc-register`, which runs after bootstrap. By then
# it has had the whole bootstrap to come up.
INFRA_WAIT := $(ENTRY) $(PIECE_A) $(PIECE_B) $(PIECE_C) $(PIECE_E) $(PIECE_F) $(PIECE_G)

# Consumer groups, for the lag report. Must match the consumer-group section of .env.example.
GROUPS := cache-updater driver-service passenger-service dispatch-service city-service clickhouse-sink

# --- output -------------------------------------------------------------------
B := \033[1m
G := \033[32m
Y := \033[33m
R := \033[31m
C := \033[36m
X := \033[0m
say = @printf "$(B)$(C)==>$(X) $(B)%s$(X)\n" "$(1)"
ok  = @printf "  $(G)ok$(X)      %s\n" "$(1)"
warn= @printf "  $(Y)warn$(X)    %s\n" "$(1)"
bad = @printf "  $(R)FAIL$(X)    %s\n" "$(1)"

# Read one literal setting out of .env (no ${...} expansion — ports only).
# Optional second argument: a fallback if the var is genuinely absent from
# .env — not unset, absent. This happens whenever a new port variable lands
# in .env.example after a real .env already exists: `make init` correctly
# refuses to overwrite it (it holds real passwords), so the new line never
# arrives there even though docker-compose.yaml's own ${VAR:-default} means
# the actual deployment is fine. Without this, `make urls` prints a blank
# instead of the port that is genuinely in use.
getenv = $(or $(shell grep -E "^$(1)=" $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-),$(2))

.PHONY: help
help:
	@printf "\n$(B)not-uber-service$(X)\n\n"
	@printf "  First time:\n"
	@printf "    $(C)make init$(X)        create .env and the shared network\n"
	@printf "    edit .env                set the passwords in section 1\n"
	@printf "    $(C)make prepare$(X)     pull images, build, fetch the map. Needs the network\n"
	@printf "    $(C)make up$(X)          start the stack, in order\n"
	@printf "    $(C)make etcd-existing$(X)  local only. Do not commit etcd.env\n\n"
	@printf "  Code change, keep the data:\n"
	@printf "    $(C)make build && make reload SVC=driver-service$(X)\n\n"
	@printf "  Code change, empty databases:\n"
	@printf "    $(C)make build && make destroy && make up && make etcd-existing$(X)\n"
	@printf "    $(C)make up$(X) does not rebuild. Skip $(C)make prepare$(X) unless the map files are missing.\n\n"
	@printf "  Look:\n"
	@printf "    $(C)make ps$(X)          what is running\n"
	@printf "    $(C)make health$(X)      health, restarts, OOM\n"
	@printf "    $(C)make errors$(X)      what actually failed\n"
	@printf "    $(C)make stats$(X)       CPU and memory against each limit\n"
	@printf "    $(C)make logs SVC=driver-service$(X)\n"
	@printf "    $(C)make tail SVC=driver-service N=80$(X)\n\n"
	@printf "  Check:\n"
	@printf "    $(C)make verify$(X)      each layer, in order\n"
	@printf "    $(C)make lag$(X)         consumer lag\n"
	@printf "    $(C)make verify-quality$(X)\n\n"
	@printf "  Shell:\n"
	@printf "    $(C)make psql$(X)        the leader          $(C)make redis-cli$(X)\n"
	@printf "    $(C)make psql-read$(X)   the replicas        $(C)make ch-client$(X)\n\n"
	@printf "  Stop:\n"
	@printf "    $(C)make stop$(X)        stop, keep data\n"
	@printf "    $(C)make down$(X)        remove containers, keep data\n"
	@printf "    $(C)make destroy$(X)     remove containers and data volumes. Keeps the map\n\n"

# =============================================================================
# Setup
# =============================================================================

include mk/init.mk

.PHONY: prepare
prepare:
	$(call say,Fetching everything the stack needs)
	@printf "  This is the only step that reaches outside this host. After it the\n"
	@printf "  stack is self-contained: the containers talk to each other on %s,\n" "$(NETWORK)"
	@printf "  and every runtime download — LION, TLC zones/trips, OSM tiles — is done\n"
	@printf "  here, once, and cached under nus-lion-data and nus-tiles-data.\n\n"
	@$(MAKE) --no-print-directory pull
	@$(MAKE) --no-print-directory build
	@$(MAKE) --no-print-directory lion-fetch
	@$(MAKE) --no-print-directory tlc-zones-fetch
	@$(MAKE) --no-print-directory tlc-trips-fetch
	@$(MAKE) --no-print-directory lion-prepare
	@$(MAKE) --no-print-directory zone-demand-prepare
	@$(MAKE) --no-print-directory tiles-prepare
	@printf "\n$(G)$(B)Ready.$(X) Run $(C)make up$(X) — it needs no network access.\n"
	@printf "  Images, the LION graph, and nyc.mbtiles are on disk. make destroy keeps the last two.\n\n"

include mk/preflight.mk

.PHONY: pull
pull:
	$(call say,Pulling pinned images)
	@$(COMPOSE) --profile init pull --ignore-buildable

.PHONY: build
build:
	$(call say,Building images)
	@printf "  Layer cache is the contract: lockfiles first, source after.\n"
	@printf "  Never --no-cache, never --pull — $(C)make pull$(X) is the registry step.\n"
	@printf "  $(C)make up$(X) does not rebuild. Run this before destroy+up when Python or routing changed.\n"
	@$(COMPOSE) build
	$(call ok,images rebuilt — the next make up will run these, not the previous tags)

# =============================================================================
# Deploy
# =============================================================================

.PHONY: up
up:
	@$(MAKE) --no-print-directory _mk-data-tree
	@$(MAKE) --no-print-directory preflight
	@$(MAKE) --no-print-directory volume-perms
	@$(MAKE) --no-print-directory lb-config
	@$(MAKE) --no-print-directory certgen
	@$(MAKE) --no-print-directory ch-secrets
	@$(MAKE) --no-print-directory ksqldb-secrets
	$(call say,Starting the infrastructure — pieces a to g)
	@$(COMPOSE) up -d $(PIECE_D)
	@$(COMPOSE) up -d --wait --wait-timeout $(WAIT_TIMEOUT) $(INFRA_WAIT)
	$(call ok,infrastructure healthy; Debezium Connect still loading its plugins)
	@$(MAKE) --no-print-directory topics
	@$(MAKE) --no-print-directory schemas
	@$(MAKE) --no-print-directory ksql-ddl
	@$(MAKE) --no-print-directory ch-ddl
	@$(MAKE) --no-print-directory superset-init
	@$(MAKE) --no-print-directory bootstrap
	@$(MAKE) --no-print-directory cdc-register
	$(call say,Starting the services — pieces i to o)
	@$(COMPOSE) up -d $(SERVICES)
	@printf "\n$(G)$(B)The stack is up.$(X)\n\n"
	@printf "  Grafana maps are same-origin OSM at /tiles/ (nus-tiles). No MapTiler key.\n"
	@printf "  $(C)make up$(X) does not rebuild images — that was $(C)make build$(X).\n\n"
	@printf "  One step is left, and it is $(B)local state you must never commit$(X):\n"
	@printf "    $(C)make etcd-existing$(X)   flip the etcd cluster out of bootstrap mode\n\n"
	@printf "  Then: $(C)make verify$(X) (includes tiles through HAProxy), $(C)make urls$(X), $(C)make lag$(X)\n"
	@printf "  After a few minutes of live trips: $(C)make verify-positions$(X)\n"
	@printf "  Do not commit $(ETCD_ENV).\n\n"

.PHONY: up-piece
up-piece:
	@case "$(PIECE)" in \
		a) set -- $(ENTRY) $(PIECE_A) ;; \
		b) set -- $(PIECE_B) ;; \
		c) set -- $(PIECE_C) ;; \
		d) set -- $(PIECE_D) ;; \
		e) set -- $(PIECE_E) ;; \
		f) set -- $(PIECE_F) ;; \
		g) set -- $(PIECE_G) ;; \
		infra) set -- $(INFRA) ;; \
		services) $(COMPOSE) up -d $(SERVICES); exit $$? ;; \
		"") printf "$(R)set PIECE=a..g, infra, or services$(X)\n"; exit 1 ;; \
		*) printf "$(R)unknown PIECE '$(PIECE)' — a..g, infra, or services$(X)\n"; exit 1 ;; \
	esac; \
	$(COMPOSE) up -d --wait --wait-timeout $(WAIT_TIMEOUT) "$$@"

.PHONY: restart
restart:
	@test -n "$(SVC)" || { printf "$(R)set SVC=<service>$(X)\n"; exit 1; }
	@$(COMPOSE) restart $(SVC)

# `docker compose restart` restarts the container that is already there - it
# does NOT look at the image again, so `make build && make restart` silently
# keeps running the old code. That failure mode has already cost this project
# a full debugging round once (a fix that was committed, built, and never
# actually running). This is the target to use after `make build` when the
# data must survive: --force-recreate replaces the container from the image
# that was just built, --no-deps leaves the healthy infrastructure alone.
.PHONY: reload
reload:
	@test -n "$(SVC)" || { printf "$(R)set SVC=<service>$(X)\n"; exit 1; }
	@$(COMPOSE) up -d --no-deps --force-recreate $(SVC)
	@docker inspect $(SVC) --format '  {{.Name}} now running image {{.Image}} started {{.State.StartedAt}}' 2>/dev/null || true

# =============================================================================
# One-shots. make up calls these. They are not separate commands.
# =============================================================================

.PHONY: volume-perms
volume-perms:
	$(call say,Data directory ownership)
	@$(COMPOSE) run --rm volume-perms

include mk/volume-quotas.mk

.PHONY: lb-config
lb-config:
	$(call say,Entry tier config)
	@$(COMPOSE) run --rm haproxy-config-render

# =============================================================================
# Verify
# =============================================================================

.PHONY: verify
verify: verify-pg verify-redis verify-kafka verify-ksqldb verify-cdc verify-ch verify-dash verify-data

.PHONY: verify-dash
verify-dash: grafana-health tiles-health verify-grafana superset-health verify-superset

# verify-walk and verify-sample are the only WORKSTATION targets in this
# file. Every other target drives Docker and runs anywhere; these two run
# pytest directly and need uv to build the test venv.
#
# Deliberately NOT added to preflight: preflight runs inside `make up` on the
# deployment host, which has no uv and does not need one. A uv check there
# would fail the bring-up over a tool the stack never uses.
.PHONY: _need-uv
_need-uv:
	@command -v uv >/dev/null 2>&1 || { \
		printf "  $(R)FAIL$(X)    uv is not on PATH, so the test venv cannot be built\n\n"; \
		printf "  This is a $(B)workstation$(X) target, not a deployment one. It runs\n"; \
		printf "  pytest against the shipped library code and needs no containers.\n"; \
		printf "  Run it where you edit the code, or install uv on this machine:\n"; \
		printf "    $(C)curl -LsSf https://astral.sh/uv/install.sh | sh$(X)\n\n"; \
		exit 1; }

.PHONY: verify-walk
verify-walk: _need-uv
	$(call say,Walk checks)
	@cd z-lib/nus-common && uv sync --extra test >/dev/null
	@PYTHONPATH="z-lib/nus-common:j-service-driver:l-service-dispatch:n-service-clickhouse-sink" \
		z-lib/nus-common/.venv/bin/pytest -c pytest.ini z-lib/nus-common/tests j-service-driver/tests l-service-dispatch/tests

.PHONY: verify-sample
verify-sample: _need-uv
	$(call say,Sample stores)
	@cd z-lib/nus-common && uv sync --extra test >/dev/null
	@PYTHONPATH="z-lib/nus-common:j-service-driver:l-service-dispatch:n-service-clickhouse-sink" \
		z-lib/nus-common/.venv/bin/pytest -c pytest.ini z-config/tests

include mk/wait-live.mk

.PHONY: verify-positions
verify-positions:
	$(call say,Positions)
	@docker inspect driver-service --format '  driver-service  started {{.State.StartedAt}}' 2>/dev/null || printf '  driver-service  not running\n'
	@docker inspect dispatch-service --format '  dispatch-service started {{.State.StartedAt}}' 2>/dev/null || printf '  dispatch-service not running\n'
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" \
		--password "$(call getenv,CH_PASSWORD)" --database nus --multiquery \
		--output-format PrettyCompactMonoBlock < z-config/check-positions.sql
	@$(COMPOSE) exec -T pg-1 psql -U postgres -d "$(call getenv,PG_DATABASE,nus)" -c \
		"SELECT round((avg(ST_Length(route::geography))/1000)::numeric, 2) AS route_km, round((avg(ST_Distance(pickup_point::geography, dropoff_point::geography))/1000)::numeric, 2) AS chord_km, round(avg(ST_NPoints(route))) AS avg_vertices FROM trips WHERE route IS NOT NULL AND ended_at > now() - interval '1 hour';"
	@$(COMPOSE) exec -T pg-1 psql -U postgres -d "$(call getenv,PG_DATABASE,nus)" \
		-f - < z-config/check-on-network.sql

# =============================================================================
# Operate
# =============================================================================

.PHONY: ps
ps:
	@$(COMPOSE) ps

.PHONY: health
health:
	@printf "  %-22s %-12s %-9s %s\n" CONTAINER HEALTH RESTARTS OOMKILLED
	@$(COMPOSE) ps -aq | while read -r c; do \
		docker inspect -f '  {{printf "%-22s" (slice .Name 1)}} {{if .State.Health}}{{printf "%-12s" .State.Health.Status}}{{else}}{{printf "%-12s" .State.Status}}{{end}} {{printf "%-9d" .RestartCount}} {{.State.OOMKilled}}' "$$c" 2>/dev/null; \
	done

include mk/errors.mk

.PHONY: stats
stats:
	@docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' $$($(COMPOSE) ps -q) 2>/dev/null || true

.PHONY: lag
lag:
	$(call say,Consumer lag)
	@for g in $(GROUPS); do \
		printf "\n  $(B)%s$(X)\n" "$$g"; \
		$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
			--bootstrap-server nus-kafka-1:9092 --describe --group "$$g" 2>/dev/null \
			| awk 'NR==1 || $$6 != "-"' | sed 's/^/    /' || printf "    (no such group yet)\n"; \
	done
	@printf "\n  Lag that stays small, or returns to small after a burst, is healthy.\n"
	@printf "  Lag that only grows is not — turn the pacing down in $(ENV_FILE).\n"

include mk/quality.mk

.PHONY: logs
logs:
	@if [ -n "$(SVC)" ]; then $(COMPOSE) logs -f --tail=200 $(SVC); else $(COMPOSE) logs -f --tail=50; fi

# logs follows, so piping it into grep|tail hangs for ever - tail cannot
# print until it sees an EOF that a follow never sends. That is not an
# obvious failure while watching it: the terminal simply sits there, and
# it reads exactly like a stalled stack. This is the one to pipe.
.PHONY: tail
tail:
	@if [ -n "$(SVC)" ]; then $(COMPOSE) logs --tail=$(or $(N),200) $(SVC); \
	else $(COMPOSE) logs --tail=$(or $(N),50); fi

include mk/urls.mk

# =============================================================================
# Teardown
# =============================================================================

.PHONY: stop
stop:
	@$(COMPOSE) stop

.PHONY: down
down:
	@$(COMPOSE) down

.PHONY: destroy
destroy:
	@printf "$(R)$(B)This deletes the databases, the cache, the topics and the warehouse.$(X)\n"
	@printf "  LION and OSM tiles (nus-lion-data, nus-tiles-data) are kept.\n"
	@printf "  $(C)make nuke$(X) is what removes those downloads.\n"
	@read -r -p "Type 'destroy' to confirm: " a; [ "$$a" = destroy ] || { echo "cancelled"; exit 1; }
	@$(COMPOSE) down -v
	@$(MAKE) --no-print-directory _rm-data-tree
	@$(MAKE) --no-print-directory etcd-new
	@printf "\n  Volumes gone and etcd put back to 'new'. LION and OSM tiles were kept.\n"
	@printf "  Next, without re-downloading: $(C)make build && make up && make etcd-existing$(X)\n"
	@printf "  Skip $(C)make prepare$(X) unless the graph or nyc.mbtiles is missing.\n"

.PHONY: nuke
nuke:
	@$(MAKE) --no-print-directory destroy
	@$(MAKE) --no-print-directory _rm-data-tree RM_MAP=1
	@docker network rm $(NETWORK) 2>/dev/null || true
	@printf "  Network $(NETWORK) removed, and the street map with it.\n"

include mk/clean.mk
