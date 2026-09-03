# =============================================================================
# not-uber-service — stack control
# =============================================================================
# Everything runs from this directory. `make` on its own prints the help.
#
# This file is the runbook made executable: the same order, the same one-shots
# at the same points. What it adds is that the order cannot be got wrong by
# accident — several steps only work if something else has happened first, and
# `make up` puts them where they belong.
#
# Full explanation of each piece: README.md. Architecture: ../README.md
# =============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE  := docker compose
NETWORK  := nus-backbone
ENV_FILE := .env
EXAMPLE  := .env.example
ETCD_ENV := a-infra-postgres/etcd.env

# How long to wait for a group of containers to report healthy. ClickHouse and
# Superset have a 40-60s start_period of their own, so this is generous.
WAIT_TIMEOUT ?= 420

# --- the stack, in the pieces the runbook names -------------------------------
ENTRY    := lb-a lb-b
PIECE_A  := etcd-1 etcd-2 etcd-3 pg-1 pg-2 pg-3
PIECE_B  := redis-1 redis-2 redis-3 sentinel-1 sentinel-2 sentinel-3
PIECE_C  := kafka-1 kafka-2 kafka-3 schema-registry
PIECE_D  := debezium-connect
PIECE_E  := ch-keeper-1 ch-keeper-2 ch-keeper-3 ch-s1r1 ch-s1r2 ch-s2r1 ch-s2r2
PIECE_F  := grafana
PIECE_G  := superset
SERVICES := cache-updater driver-service passenger-service dispatch-service city-service clickhouse-sink
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
getenv = $(shell grep -E "^$(1)=" $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-)

.PHONY: help
help:
	@printf "\n$(B)not-uber-service$(X) — a cab platform's whole data lifecycle, in containers\n\n"
	@printf "  First deployment, in three commands:\n"
	@printf "    $(C)make init$(X)        create .env and the shared network\n"
	@printf "    $(C)\$$EDITOR .env$(X)     change every password in section 1\n"
	@printf "    $(C)make up$(X)          preflight, then the whole ordered bring-up\n\n"
	@awk 'BEGIN {FS = ":.*##"} \
		/^##@/ { printf "\n$(B)%s$(X)\n", substr($$0, 5) } \
		/^[a-zA-Z0-9_-]+:.*?##/ { printf "  $(C)%-16s$(X) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\n"

# =============================================================================
##@ Setup
# =============================================================================

.PHONY: init
init: ## Create .env from the template and the shared nus-backbone network
	$(call say,Preparing the working copy)
	@if [ -f $(ENV_FILE) ] && [ -z "$(FORCE)" ]; then \
		printf "  $(Y)kept$(X)    $(ENV_FILE) already exists — not overwriting it\n"; \
		if ! grep -qE '^PG_SUPERUSER_PASSWORD=' $(ENV_FILE); then \
			printf "  $(R)but$(X)     it predates the master settings file: it has no passwords in it.\n"; \
			printf "              Every component now resolves from this one file, so it needs\n"; \
			printf "              the full template. Back it up and regenerate:\n"; \
			printf "                $(C)cp $(ENV_FILE) $(ENV_FILE).bak && make init FORCE=1$(X)\n"; \
		fi; \
	elif [ -f $(ENV_FILE) ]; then \
		cp $(ENV_FILE) $(ENV_FILE).bak; \
		cp $(EXAMPLE) $(ENV_FILE); \
		printf "  $(G)created$(X) $(ENV_FILE) from $(EXAMPLE) (previous kept as $(ENV_FILE).bak)\n"; \
	else \
		cp $(EXAMPLE) $(ENV_FILE); \
		printf "  $(G)created$(X) $(ENV_FILE) from $(EXAMPLE)\n"; \
	fi
	@if docker network inspect $(NETWORK) >/dev/null 2>&1; then \
		printf "  $(G)ok$(X)      network $(NETWORK) exists\n"; \
	else \
		docker network create $(NETWORK) >/dev/null; \
		printf "  $(G)created$(X) network $(NETWORK)\n"; \
	fi
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -z "$$root" ]; then \
		printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT is not set in $(ENV_FILE)\n"; exit 1; \
	elif ! mkdir -p "$$root" 2>/dev/null; then \
		printf "  $(R)FAIL$(X)    cannot create %s\n" "$$root"; \
		printf "              This is where all 26 data volumes live, set as NUS_VOLUME_ROOT\n"; \
		printf "              in $(ENV_FILE). Either the parent directory does not exist, or\n"; \
		printf "              you do not have permission to write there. Fix one of:\n"; \
		printf "                $(C)sudo mkdir -p %s && sudo chown $$(id -un) %s$(X)\n" "$$root" "$$root"; \
		printf "                or point NUS_VOLUME_ROOT at a path you can write\n"; \
		exit 1; \
	else \
		for d in $$($(COMPOSE) config 2>/dev/null | grep -oE 'device: .+' | cut -d' ' -f2- || true); do \
			mkdir -p "$$d"; done; \
		made=$$(find "$$root" -maxdepth 1 -mindepth 1 -type d -name 'nus-*' | wc -l); \
		free=$$(df -BG --output=avail "$$root" 2>/dev/null | tail -1 | tr -dc '0-9'); \
		printf "  $(G)ok$(X)      %s data directories under %s (%s GB free)\n" "$$made" "$$root" "$$free"; \
	fi
	@printf "\n  Now edit $(B)$(ENV_FILE)$(X) — section 1 holds every password.\n"
	@printf "  Then: $(C)make preflight$(X), and $(C)make up$(X).\n\n"

.PHONY: preflight
preflight: ## Check the host, Docker and the settings before anything is started
	$(call say,Preflight)
	@fail=0; \
	command -v docker >/dev/null 2>&1 \
		&& printf "  $(G)ok$(X)      docker %s\n" "$$(docker version -f '{{.Server.Version}}' 2>/dev/null || echo '(daemon unreachable)')" \
		|| { printf "  $(R)FAIL$(X)    docker is not installed\n"; fail=1; }; \
	docker info >/dev/null 2>&1 \
		|| { printf "  $(R)FAIL$(X)    the Docker daemon is not reachable (are you root / in the docker group?)\n"; fail=1; }; \
	cv=$$($(COMPOSE) version --short 2>/dev/null || echo none); \
	case "$$cv" in none) printf "  $(R)FAIL$(X)    the compose plugin is missing\n"; fail=1;; \
		1.*) printf "  $(R)FAIL$(X)    compose $$cv is v1; this stack needs v2+ for include:\n"; fail=1;; \
		*)   printf "  $(G)ok$(X)      compose %s\n" "$$cv";; esac; \
	cores=$$(nproc); mem=$$(awk '/MemTotal/{printf "%d", $$2/1024/1024}' /proc/meminfo); \
	[ "$$cores" -ge 20 ] && printf "  $(G)ok$(X)      %s cores (budget assumes 20)\n" "$$cores" \
		|| printf "  $(Y)warn$(X)    %s cores; the budget assumes 20 — turn the pacing down\n" "$$cores"; \
	[ "$$mem" -ge 118 ] && printf "  $(G)ok$(X)      %s GB RAM (budget needs ~112 GB at the bootstrap peak)\n" "$$mem" \
		|| printf "  $(Y)warn$(X)    %s GB RAM; the budget peaks near 112 GB during bootstrap\n" "$$mem"; \
	root=$$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker); \
	avail=$$(df -BG --output=avail "$$root" 2>/dev/null | tail -1 | tr -dc '0-9'); \
	if [ -n "$$avail" ] && [ "$$avail" -ge 100 ]; then \
		printf "  $(G)ok$(X)      %s GB free on %s\n" "$$avail" "$$root"; \
	else \
		printf "  $(Y)warn$(X)    only %s GB free on %s — images are ~15 GB and ClickHouse grows ~1-2 GB/day.\n" "$$avail" "$$root"; \
		printf "              Move Docker's data-root to a big disk before deploying.\n"; \
	fi; \
	if [ ! -f $(ENV_FILE) ]; then \
		printf "  $(R)FAIL$(X)    no $(ENV_FILE) — run 'make init' first\n"; fail=1; \
	else \
		printf "  $(G)ok$(X)      $(ENV_FILE) present\n"; \
		left=$$(grep -cE '^[A-Z_]+=.*change-me' $(ENV_FILE) || true); \
		if [ "$$left" -gt 0 ]; then \
			printf "  $(R)FAIL$(X)    %s setting(s) still hold a change-me placeholder:\n" "$$left"; \
			grep -nE '^[A-Z_]+=.*change-me' $(ENV_FILE) | sed 's/=.*/=.../' | sed 's/^/              /'; \
			fail=1; \
		else printf "  $(G)ok$(X)      no change-me placeholders left\n"; fi; \
		missing=""; \
		for v in PG_SUPERUSER_PASSWORD PG_REPLICATION_PASSWORD PATRONI_REST_PASSWORD REDIS_PASSWORD \
		         CH_PASSWORD GRAFANA_ADMIN_PASSWORD SUPERSET_ADMIN_PASSWORD SUPERSET_SECRET_KEY KAFKA_CLUSTER_ID; do \
			grep -qE "^$$v=.+" $(ENV_FILE) || missing="$$missing $$v"; done; \
		[ -z "$$missing" ] && printf "  $(G)ok$(X)      every required setting has a value\n" \
			|| { printf "  $(R)FAIL$(X)    missing or empty:%s\n" "$$missing"; fail=1; }; \
		dupes=$$(grep -oE '^[A-Z_][A-Z0-9_]*=' $(ENV_FILE) | sort | uniq -d | tr -d '=' | tr '\n' ' ' || true); \
		[ -z "$$dupes" ] && printf "  $(G)ok$(X)      no setting is defined twice\n" \
			|| printf "  $(Y)warn$(X)    defined more than once (the last wins): %s\n" "$$dupes"; \
	fi; \
	root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -z "$$root" ]; then printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT is not set in $(ENV_FILE)\n"; fail=1; \
	elif [ ! -d "$$root" ]; then printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT %s does not exist — run 'make init'\n" "$$root"; fail=1; \
	elif [ ! -w "$$root" ]; then printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT %s is not writable\n" "$$root"; fail=1; \
	else \
		vfree=$$(df -BG --output=avail "$$root" 2>/dev/null | tail -1 | tr -dc '0-9'); \
		vdev=$$(df --output=source "$$root" 2>/dev/null | tail -1); \
		if [ -n "$$vfree" ] && [ "$$vfree" -ge 200 ]; then \
			printf "  $(G)ok$(X)      data volumes -> %s (%s GB free on %s)\n" "$$root" "$$vfree" "$$vdev"; \
		else \
			printf "  $(Y)warn$(X)    data volumes -> %s, only %s GB free — ClickHouse grows 1-2 GB/day\n" "$$root" "$$vfree"; \
		fi; \
	fi; \
	docker network inspect $(NETWORK) >/dev/null 2>&1 \
		&& printf "  $(G)ok$(X)      network $(NETWORK) exists\n" \
		|| { printf "  $(R)FAIL$(X)    network $(NETWORK) is missing — run 'make init'\n"; fail=1; }; \
	missing=""; n=0; \
	for d in $$($(COMPOSE) config 2>/dev/null | grep -oE 'device: .+' | cut -d' ' -f2- || true); do \
		n=$$((n+1)); [ -d "$$d" ] || missing="$$missing $$(basename "$$d")"; done; \
	if [ "$$n" -eq 0 ]; then :; \
	elif [ -z "$$missing" ]; then \
		printf "  $(G)ok$(X)      all %s volume directories exist\n" "$$n"; \
	else \
		printf "  $(R)FAIL$(X)    volume directories the stack mounts do not exist:\n"; \
		for m in $$missing; do printf "                %s\n" "$$m"; done; \
		printf "              Run $(C)make init$(X) — it creates exactly the paths the compose\n"; \
		printf "              model mounts. A container cannot start without its own.\n"; \
		fail=1; \
	fi; \
	probe=$$(grep -E '^BUSYBOX_IMAGE=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	probe=$${probe:-busybox:1.38.0}; \
	docker image inspect "$$probe" >/dev/null 2>&1 || docker pull -q "$$probe" >/dev/null 2>&1 || true; \
	if ! docker image inspect "$$probe" >/dev/null 2>&1; then \
		printf "  $(Y)warn$(X)    could not fetch %s, so container networking was not checked\n" "$$probe"; \
	elif docker run --rm "$$probe" sh -c 'nslookup deb.debian.org >/dev/null 2>&1' >/dev/null 2>&1; then \
		if docker run --rm "$$probe" sh -c 'wget -q -T5 -O- http://deb.debian.org/ >/dev/null 2>&1' >/dev/null 2>&1; then \
			printf "  $(G)ok$(X)      containers can resolve DNS and reach the internet\n"; \
		elif [ "$$(grep -E '^BUILD_NETWORK=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-)" = host ]; then \
			printf "  $(Y)warn$(X)    containers cannot reach the internet, but BUILD_NETWORK=host is set,\n"; \
			printf "              so the image builds will use the host's network and succeed.\n"; \
			printf "              $(Y)One runtime fetch is still not covered:$(X) h-bootstrap downloads the\n"; \
			printf "              street map from download.geofabrik.de, in a container. Fetch it on\n"; \
			printf "              the host first and bootstrap will skip the download:\n"; \
			printf "                $(C)make osm-fetch$(X)\n"; \
		else \
			printf "  $(R)FAIL$(X)    containers resolve DNS but cannot reach the internet.\n"; \
			printf "              Image pulls still work (the daemon fetches those over the host's\n"; \
			printf "              own stack), but every image built here installs packages, and that\n"; \
			printf "              runs inside a container.\n"; \
			printf "              FIRST, check the host. If it cannot reach them either, this is not\n"; \
			printf "              a Docker problem at all:\n"; \
			printf "                $(C)curl -sS -m10 -I http://deb.debian.org/ | head -1$(X)\n"; \
			printf "              Then read the symptom, which names the fault:\n"; \
			printf "                'Connection refused'     something is actively rejecting it —\n"; \
			printf "                                         upstream filtering or a transparent proxy\n"; \
			printf "                hangs, then times out    MTU mismatch, or a firewall DROP\n"; \
			printf "                'Network is unreachable' routing, or net.ipv4.ip_forward is 0\n"; \
			printf "              Reproduce it directly:\n"; \
			printf "                $(C)docker run --rm %s wget -O- -T8 http://deb.debian.org/$(X)\n" "$$probe"; \
			printf "              If the host CAN reach them and only containers cannot, its route out\n"; \
			printf "              captures host traffic but not forwarded traffic (sshuttle and similar).\n"; \
			printf "              Then set $(C)BUILD_NETWORK=host$(X) in $(ENV_FILE) and re-run.\n"; \
			fail=1; \
		fi; \
	else \
		printf "  $(R)FAIL$(X)    containers cannot resolve DNS, so no image can be built.\n"; \
		printf "              Image pulls still work (the daemon uses the host's stack), so this\n"; \
		printf "              looks fine until the first build. Usual causes:\n"; \
		printf "                net.ipv4.ip_forward is 0   -> sysctl -w net.ipv4.ip_forward=1\n"; \
		printf "                iptables FORWARD drops     -> iptables -S FORWARD | head\n"; \
		printf "                no resolver in the container -> add \"dns\" to /etc/docker/daemon.json\n"; \
		printf "              Reproduce it directly:\n"; \
		printf "                $(C)docker run --rm %s nslookup deb.debian.org$(X)\n" "$$probe"; \
		fail=1; \
	fi; \
	$(COMPOSE) config -q 2>/dev/null \
		&& printf "  $(G)ok$(X)      all 14 components resolve from $(ENV_FILE)\n" \
		|| { printf "  $(R)FAIL$(X)    the compose model does not resolve:\n"; $(COMPOSE) config -q 2>&1 | sed 's/^/              /'; fail=1; }; \
	if command -v ss >/dev/null 2>&1; then listening=$$(ss -ltn 2>/dev/null | awk 'NR>1{print $$4}' || true); \
	elif command -v netstat >/dev/null 2>&1; then listening=$$(netstat -ltn 2>/dev/null | awk 'NR>2{print $$4}' || true); \
	else listening=""; fi; \
	if [ -z "$$listening" ]; then \
		printf "  $(Y)warn$(X)    no ss or netstat here, so host ports were not checked\n"; \
	else \
		ours=$$(docker ps --filter "label=com.docker.compose.project=not-uber-service" \
			--format '{{.Ports}}' 2>/dev/null | grep -oE '[0-9]+->' | tr -d '>-' | sort -u || true); \
		busy=""; mine=""; \
		for p in $$(grep -oE '^LB_[AB]_[A-Z_]+PORT=[0-9]+' $(ENV_FILE) 2>/dev/null | cut -d= -f2); do \
			echo "$$listening" | grep -qE "[:.]$$p\$$" || continue; \
			if echo "$$ours" | grep -qx "$$p"; then mine="$$mine $$p"; else busy="$$busy $$p"; fi; done; \
		if [ -n "$$busy" ]; then \
			printf "  $(R)FAIL$(X)    host ports held by something else:%s — change them in $(ENV_FILE)\n" "$$busy"; fail=1; \
		elif [ -n "$$mine" ]; then \
			printf "  $(G)ok$(X)      published ports are held by this stack's own containers (already up)\n"; \
		else \
			printf "  $(G)ok$(X)      every published host port is free\n"; \
		fi; \
	fi; \
	state=$$(grep -E '^ETCD_INITIAL_CLUSTER_STATE=' $(ETCD_ENV) | cut -d= -f2); \
	if docker volume inspect nus-etcd-data-1 >/dev/null 2>&1; then \
		[ "$$state" = existing ] && printf "  $(G)ok$(X)      etcd state 'existing' and the data volumes are there\n" \
			|| printf "  $(Y)warn$(X)    etcd volumes exist but state is '$$state' — run 'make etcd-existing' after this bring-up\n"; \
	else \
		[ "$$state" = new ] && printf "  $(G)ok$(X)      etcd state 'new' for a first bootstrap\n" \
			|| { printf "  $(R)FAIL$(X)    etcd state is '$$state' but there are no data volumes; it must be 'new' to bootstrap\n"; fail=1; }; \
	fi; \
	echo; \
	if [ "$$fail" -ne 0 ]; then printf "$(R)$(B)preflight failed — fix the above before deploying.$(X)\n\n"; exit 1; \
	else printf "$(G)$(B)preflight passed.$(X)\n\n"; fi

.PHONY: pull
pull: ## Pull every pinned image (do this first on a new host — tags do move)
	$(call say,Pulling pinned images)
	@$(COMPOSE) --profile init pull --ignore-buildable

.PHONY: build
build: ## Build the six images this repo defines
	$(call say,Building images)
	@$(COMPOSE) build

.PHONY: config
config: ## Print the fully merged compose model (what Docker actually sees)
	@$(COMPOSE) config

# =============================================================================
##@ Deploy
# =============================================================================

.PHONY: up
up: preflight ## THE FIRST DEPLOYMENT: every piece, in order, with the one-shots
	@$(MAKE) --no-print-directory volume-perms
	@$(MAKE) --no-print-directory certgen
	$(call say,Starting the infrastructure — pieces a to g)
	@$(COMPOSE) up -d --build $(PIECE_D)
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(INFRA_WAIT)
	$(call ok,infrastructure healthy; Debezium Connect still loading its plugins)
	@$(MAKE) --no-print-directory topics
	@$(MAKE) --no-print-directory ch-ddl
	@$(MAKE) --no-print-directory superset-init
	@$(MAKE) --no-print-directory bootstrap
	@$(MAKE) --no-print-directory cdc-register
	$(call say,Starting the six services — pieces i to n)
	@$(COMPOSE) up -d --build $(SERVICES)
	@printf "\n$(G)$(B)The stack is up.$(X)\n\n"
	@printf "  One step is left, and it is $(B)local state you must never commit$(X):\n"
	@printf "    $(C)make etcd-existing$(X)   flip the etcd cluster out of bootstrap mode\n\n"
	@printf "  Then: $(C)make verify$(X), $(C)make urls$(X), $(C)make lag$(X)\n\n"

.PHONY: up-infra
up-infra: ## Start pieces a to g only (no bootstrap, no services)
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(INFRA)

.PHONY: up-services
up-services: ## Start pieces i to n only
	@$(COMPOSE) up -d --build $(SERVICES)

.PHONY: up-a up-b up-c up-d up-e up-f up-g
up-a: ## Piece a — PostgreSQL (Patroni) + etcd, behind the entry tier
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(ENTRY) $(PIECE_A)
up-b: ## Piece b — Redis + Sentinel
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(PIECE_B)
up-c: ## Piece c — Kafka + Schema Registry
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(PIECE_C)
up-d: ## Piece d — Debezium Connect
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(PIECE_D)
up-e: ## Piece e — ClickHouse + Keeper
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(PIECE_E)
up-f: ## Piece f — Grafana
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(PIECE_F)
up-g: ## Piece g — Superset
	@$(COMPOSE) up -d --build --wait --wait-timeout $(WAIT_TIMEOUT) $(PIECE_G)

.PHONY: restart
restart: ## Restart one service:  make restart SVC=driver-service
	@test -n "$(SVC)" || { printf "$(R)set SVC=<service>$(X)\n"; exit 1; }
	@$(COMPOSE) restart $(SVC)

# =============================================================================
##@ One-shots (idempotent — safe to re-run at any time)
# =============================================================================

.PHONY: certgen
certgen: ## Generate the etcd TLS material (skips itself once the certs exist)
	$(call say,etcd TLS certificates)
	@$(COMPOSE) run --rm etcd-certgen

.PHONY: volume-perms
volume-perms: ## Hand each data directory to the user that writes to it (BEFORE first start)
	$(call say,Data directory ownership)
	@$(COMPOSE) run --rm volume-perms

.PHONY: kafka-dirs
kafka-dirs: ## Hand the Kafka volumes to the user inside the image (covered by volume-perms)
	$(call say,Kafka data directory ownership)
	@$(COMPOSE) run --rm kafka-dirs

.PHONY: topics
topics: ## Create the Kafka topics from topics.tsv (after the brokers are healthy)
	$(call say,Kafka topics)
	@$(COMPOSE) run --rm kafka-topics-init

.PHONY: ch-ddl
ch-ddl: ## Create the ClickHouse tables (after the four nodes are healthy)
	$(call say,ClickHouse tables)
	@$(COMPOSE) run --rm ch-ddl-init

.PHONY: superset-init
superset-init: ## Superset's own tables, admin user, roles and ClickHouse connection
	$(call say,Superset initialisation)
	@$(COMPOSE) run --rm superset-init

.PHONY: bootstrap
bootstrap: ## Run h-bootstrap and follow it (the map import is the long quiet part)
	$(call say,Bootstrap — schema, street map, people, a week of history)
	@printf "  This takes a while: the OSM import is the slow step.\n"
	@$(COMPOSE) up -d --build bootstrap
	@$(COMPOSE) logs -f bootstrap & LOGS=$$!; \
	code=$$(docker wait bootstrap 2>/dev/null || echo 1); \
	kill $$LOGS 2>/dev/null || true; wait $$LOGS 2>/dev/null || true; \
	if [ "$$code" = 0 ]; then printf "\n  $(G)ok$(X)      bootstrap finished; system:bootstrap:done is set\n"; \
	else printf "\n  $(R)FAIL$(X)    bootstrap exited $$code — the services stay in standby on purpose.\n"; \
	     printf "              Read the log above, fix it, then: make bootstrap\n"; exit 1; fi

.PHONY: osm-fetch
osm-fetch: ## Download the street map on the HOST, so bootstrap does not have to
	$(call say,Fetching the street map on the host)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	url=$$(grep -E '^OSM_URL=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	dest="$$root/nus-osm-data"; file="$$dest/region-latest.osm.pbf"; \
	mkdir -p "$$dest"; \
	if [ -s "$$file" ]; then \
		printf "  $(G)ok$(X)      already there: %s (%s)\n" "$$file" "$$(du -h "$$file" | cut -f1)"; \
	else \
		printf "  from    %s\n  to      %s\n" "$$url" "$$file"; \
		curl -fL --progress-bar -o "$$file.part" "$$url" || { rm -f "$$file.part"; \
			printf "  $(R)FAIL$(X)    download failed. This runs on the HOST, so if it cannot reach\n"; \
			printf "              Geofabrik either, neither will bootstrap.\n"; exit 1; }; \
		mv "$$file.part" "$$file"; \
		printf "  $(G)ok$(X)      %s\n" "$$(du -h "$$file" | cut -f1)"; \
	fi; \
	$(MAKE) --no-print-directory volume-perms >/dev/null 2>&1 || true; \
	printf "  $(G)ok$(X)      handed to the bootstrap user; it will skip the download\n"

.PHONY: cdc-register
cdc-register: ## Register the Debezium connector (only AFTER bootstrap made the tables)
	$(call say,Debezium connector)
	@$(COMPOSE) run --rm connector-register

.PHONY: etcd-existing
etcd-existing: ## Post-bootstrap: flip etcd to 'existing' (LOCAL STATE — never commit)
	$(call say,Flipping the etcd cluster state to existing)
	@sed -i 's/^ETCD_INITIAL_CLUSTER_STATE=new$$/ETCD_INITIAL_CLUSTER_STATE=existing/' $(ETCD_ENV)
	@grep -E '^ETCD_INITIAL_CLUSTER_STATE=' $(ETCD_ENV) | sed 's/^/  now: /'
	@$(COMPOSE) up -d --wait --wait-timeout 120 etcd-1 etcd-2 etcd-3
	$(call ok,etcd recreated; the data volumes were kept)
	@printf "  $(Y)Do not commit $(ETCD_ENV).$(X) A fresh clone must bootstrap from 'new';\n"
	@printf "  on a live cluster 'new' would let a member with a lost volume start its own.\n"

.PHONY: etcd-new
etcd-new: ## Put etcd back to 'new' (only before a fresh bootstrap from empty volumes)
	@sed -i 's/^ETCD_INITIAL_CLUSTER_STATE=existing$$/ETCD_INITIAL_CLUSTER_STATE=new/' $(ETCD_ENV)
	@grep -E '^ETCD_INITIAL_CLUSTER_STATE=' $(ETCD_ENV) | sed 's/^/  now: /'

# =============================================================================
##@ Verify
# =============================================================================

.PHONY: verify
verify: verify-pg verify-redis verify-kafka verify-cdc verify-ch verify-dash verify-data ## Every check below, in order

.PHONY: verify-pg
verify-pg: ## etcd health, Patroni roles, and what the proxy routes where
	$(call say,PostgreSQL)
	@$(COMPOSE) exec -T etcd-1 etcdctl \
		--endpoints=https://etcd-1:2379,https://etcd-2:2379,https://etcd-3:2379 \
		--cacert=/certs/ca.crt --cert=/certs/client.crt --key=/certs/client.key \
		endpoint health || true
	@echo
	@$(COMPOSE) exec -T pg-1 patronictl -c /etc/patroni/patroni.yml list || true
	@printf "\n  Expect exactly one Leader and two Replicas, with lag at or near 0.\n"
	@printf "  HAProxy view: http://localhost:$(call getenv,LB_A_STATS_PORT)/stats\n"
	@printf "  Red rows there are $(B)expected$(X): the checks ask which ROLE a node holds,\n"
	@printf "  so pg_write shows 1 UP and pg_read shows 2 UP. A node down in BOTH is the\n"
	@printf "  only real failure signal.\n"

.PHONY: verify-redis
verify-redis: ## Node roles, what Sentinel believes, and whether it could fail over
	$(call say,Redis)
	@for n in redis-1 redis-2 redis-3; do \
		printf "  %-9s %s\n" "$$n" "$$($(COMPOSE) exec -T $$n redis-cli -a "$$(grep -E '^REDIS_PASSWORD=' $(ENV_FILE) | cut -d= -f2-)" --no-auth-warning info replication 2>/dev/null | awk -F: '/^role:/{print $$2}' | tr -d '\r')"; \
	done
	@echo
	@$(COMPOSE) exec -T sentinel-1 redis-cli -p 26379 sentinel ckquorum nus-cache || true
	@printf "\n  Expect one master, two slaves, and a quorum that can be reached.\n"

.PHONY: verify-kafka
verify-kafka: ## Brokers, the controller quorum, and every topic's replication
	$(call say,Kafka)
	@$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-broker-api-versions.sh \
		--bootstrap-server kafka-1:9092 2>/dev/null | grep -E '^kafka-[0-9]' || true
	@echo
	@$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-1:9092 --describe 2>/dev/null | grep -E '^Topic:' || true
	@printf "\n  Every partition should list three replicas and three in-sync.\n"

.PHONY: verify-cdc
verify-cdc: ## Connector state, the cdc.* topics, and the replication slot
	$(call say,Debezium)
	@$(COMPOSE) exec -T debezium-connect curl -sS http://localhost:8083/connectors/nus-pg/status 2>/dev/null || true
	@echo
	@$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-1:9092 --list 2>/dev/null | grep '^cdc\.' || printf "  no cdc.* topics yet\n"
	@echo
	@$(COMPOSE) exec -T pg-1 psql -U postgres -c \
		"SELECT slot_name, active, wal_status FROM pg_replication_slots;" 2>/dev/null || true
	@printf "\n  The connector should be RUNNING and the slot active with wal_status 'reserved'.\n"
	@printf "  A slot that exists but is inactive makes PostgreSQL keep WAL for ever.\n"

.PHONY: verify-ch
verify-ch: ## Cluster members, Keeper, the tables, and replication delay
	$(call say,ClickHouse)
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT host_name, shard_num, replica_num FROM system.clusters WHERE cluster='nus_cluster' FORMAT PrettyCompact" 2>/dev/null || true
	@echo
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT database, name, engine FROM system.tables WHERE database='nus' ORDER BY name FORMAT PrettyCompact" 2>/dev/null || true
	@echo
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT table, absolute_delay FROM system.replicas FORMAT PrettyCompact" 2>/dev/null || true
	@printf "\n  Four members, the tables present, and absolute_delay at or near 0.\n"

.PHONY: verify-dash
verify-dash: ## Grafana and Superset answer, with their data sources registered
	$(call say,Dashboards)
	@$(COMPOSE) exec -T grafana wget -qO- http://localhost:3000/api/health 2>/dev/null || true
	@echo
	@$(COMPOSE) exec -T superset curl -sS http://localhost:8088/health 2>/dev/null || true
	@printf "\n\n  Empty panels are fine before bootstrap; an error is not.\n"

.PHONY: verify-data
verify-data: ## The marker, the seeded rows, the street graph, and the warehouse
	$(call say,Data)
	@printf "  bootstrap marker: "
	@$(COMPOSE) exec -T redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning \
		get system:bootstrap:done 2>/dev/null || printf "(not set)\n"
	@$(COMPOSE) exec -T pg-1 psql -U postgres -t -c \
		"SELECT 'drivers      ' || count(*) FROM drivers UNION ALL \
		 SELECT 'passengers   ' || count(*) FROM passengers UNION ALL \
		 SELECT 'trips        ' || count(*) FROM trips UNION ALL \
		 SELECT 'city_zones   ' || count(*) FROM city_zones UNION ALL \
		 SELECT 'road segments' || count(*) FROM ways UNION ALL \
		 SELECT 'seg. traffic ' || count(*) FROM segment_traffic;" 2>/dev/null | sed 's/^ */  /' || true
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT 'trip_events ' || toString(count()) FROM nus.trip_events" 2>/dev/null | sed 's/^/  /' || true
	@printf "\n  Trips should be roughly HISTORY_DAYS x HISTORY_TRIPS_PER_DAY, and 'ways'\n"
	@printf "  in the hundreds of thousands. 'ways' at 0 means the map import did not run.\n"

.PHONY: verify-limits
verify-limits: ## Prove the resource budget holds: nothing OOM-killed, nothing pinned
	@$(MAKE) --no-print-directory oom
	@$(MAKE) --no-print-directory stats

# =============================================================================
##@ Operate
# =============================================================================

.PHONY: ps
ps: ## What is running, and what state it is in
	@$(COMPOSE) ps

.PHONY: health
health: ## One line per container: health, restarts, and whether it was OOM-killed
	@printf "  %-22s %-12s %-9s %s\n" CONTAINER HEALTH RESTARTS OOMKILLED
	@for c in $$($(COMPOSE) ps -aq); do \
		docker inspect -f '  {{printf "%-22s" .Name}} {{printf "%-12s" (or .State.Health.Status .State.Status)}} {{printf "%-9d" .RestartCount}} {{.State.OOMKilled}}' $$c 2>/dev/null | sed 's|/||'; echo; \
	done

.PHONY: oom
oom: ## Anything killed for using too much memory, or restarting in a loop
	$(call say,Restart counts and OOM kills)
	@bad=0; for c in $$($(COMPOSE) ps -aq); do \
		read -r name count oom <<< "$$(docker inspect -f '{{.Name}} {{.RestartCount}} {{.State.OOMKilled}}' $$c | tr -d '/')"; \
		if [ "$$oom" = true ] || [ "$$count" -gt 3 ]; then \
			printf "  $(R)%-24s$(X) restarts=%s oomkilled=%s\n" "$$name" "$$count" "$$oom"; bad=1; fi; \
	done; [ "$$bad" = 0 ] && printf "  $(G)ok$(X)      nothing OOM-killed, nothing restart-looping\n" || \
		printf "\n  $(Y)An OOM kill means that component's limit or the pacing must come down.$(X)\n"

.PHONY: stats
stats: ## Live memory and CPU against each container's limit
	@docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' $$($(COMPOSE) ps -q) 2>/dev/null || true

.PHONY: lag
lag: ## Consumer lag per group — the health signal for the whole pipeline
	$(call say,Consumer lag)
	@for g in $(GROUPS); do \
		printf "\n  $(B)%s$(X)\n" "$$g"; \
		$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
			--bootstrap-server kafka-1:9092 --describe --group "$$g" 2>/dev/null \
			| awk 'NR==1 || $$6 != "-"' | sed 's/^/    /' || printf "    (no such group yet)\n"; \
	done
	@printf "\n  Lag that stays small, or returns to small after a burst, is healthy.\n"
	@printf "  Lag that only grows is not — turn the pacing down in $(ENV_FILE).\n"

.PHONY: logs
logs: ## Follow logs:  make logs  |  make logs SVC=dispatch-service
	@if [ -n "$(SVC)" ]; then $(COMPOSE) logs -f --tail=200 $(SVC); else $(COMPOSE) logs -f --tail=50; fi

.PHONY: urls
urls: ## Where to point a browser or a client
	@printf "\n  $(B)Through lb-a$(X) (canonical)          $(B)lb-b$(X) (failover twin)\n"
	@printf "  PostgreSQL writes  localhost:%-8s  localhost:%s\n" "$(call getenv,LB_A_PG_WRITE_PORT)" "$(call getenv,LB_B_PG_WRITE_PORT)"
	@printf "  PostgreSQL reads   localhost:%-8s  localhost:%s\n" "$(call getenv,LB_A_PG_READ_PORT)"  "$(call getenv,LB_B_PG_READ_PORT)"
	@printf "  ClickHouse HTTP    localhost:%-8s  localhost:%s\n" "$(call getenv,LB_A_CH_HTTP_PORT)"  "$(call getenv,LB_B_CH_HTTP_PORT)"
	@printf "  ClickHouse native  localhost:%-8s  localhost:%s\n" "$(call getenv,LB_A_CH_NATIVE_PORT)" "$(call getenv,LB_B_CH_NATIVE_PORT)"
	@printf "  Grafana            http://localhost:%-3s   http://localhost:%s\n" "$(call getenv,LB_A_GRAFANA_PORT)" "$(call getenv,LB_B_GRAFANA_PORT)"
	@printf "  Superset           http://localhost:%-3s   http://localhost:%s\n" "$(call getenv,LB_A_SUPERSET_PORT)" "$(call getenv,LB_B_SUPERSET_PORT)"
	@printf "  HAProxy stats      http://localhost:%-3s   http://localhost:%s\n\n" "$(call getenv,LB_A_STATS_PORT)" "$(call getenv,LB_B_STATS_PORT)"

.PHONY: psql psql-read redis-cli ch-client patronictl
psql: ## A psql shell on the current leader, through the proxy
	@$(COMPOSE) exec pg-1 psql "host=lb-a port=5432 user=postgres password=$(call getenv,PG_SUPERUSER_PASSWORD) dbname=postgres"
psql-read: ## A psql shell on the replica pool, through the proxy
	@$(COMPOSE) exec pg-1 psql "host=lb-a port=5433 user=postgres password=$(call getenv,PG_SUPERUSER_PASSWORD) dbname=postgres"
redis-cli: ## A redis-cli shell on the current primary
	@$(COMPOSE) exec redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning
ch-client: ## A clickhouse-client shell
	@$(COMPOSE) exec ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" --database nus
patronictl: ## patronictl, for switchover and friends:  make patronictl ARGS=list
	@$(COMPOSE) exec pg-1 patronictl -c /etc/patroni/patroni.yml $(or $(ARGS),list)

.PHONY: failover-pg failover-redis
failover-pg: ## Demo: hand the PostgreSQL leadership to another node
	@$(COMPOSE) exec pg-1 patronictl -c /etc/patroni/patroni.yml switchover --force
	@printf "\n  HAProxy follows the promotion on its own. Watch: make verify-pg\n"
failover-redis: ## Demo: ask Sentinel to promote a Redis replica
	@$(COMPOSE) exec sentinel-1 redis-cli -p 26379 sentinel failover nus-cache
	@printf "\n  Watch it land: make verify-redis\n"

# =============================================================================
##@ Teardown
# =============================================================================

.PHONY: stop
stop: ## Stop every container, keep them and the data
	@$(COMPOSE) stop

.PHONY: down
down: ## Remove the containers, KEEP the data volumes
	@$(COMPOSE) down

.PHONY: destroy
destroy: ## Remove the containers AND DESTROY every data volume
	@printf "$(R)$(B)This deletes the databases, the cache, the topics, the warehouse\n"
	@printf "and the downloaded street map.$(X)\n"
	@read -r -p "Type 'destroy' to confirm: " a; [ "$$a" = destroy ] || { echo "cancelled"; exit 1; }
	@$(COMPOSE) down -v
	@$(MAKE) --no-print-directory _rm-data-tree
	@$(MAKE) --no-print-directory etcd-new
	@printf "\n  Volumes gone and etcd put back to 'new'. The next 'make up' starts fresh.\n"

.PHONY: nuke
nuke: ## destroy EVERYTHING including the street map, and the shared network
	@$(MAKE) --no-print-directory destroy
	@$(MAKE) --no-print-directory _rm-data-tree RM_OSM=1
	@docker network rm $(NETWORK) 2>/dev/null || true
	@printf "  Network $(NETWORK) removed, and the street map with it.\n"

# The volumes are bind mounts, so `docker compose down -v` removes the volume
# entries and leaves every byte on disk. Nothing else deletes them, and a
# leftover tree is picked up by the next bring-up as if it were a fresh
# volume — a half-initialised PostgreSQL or etcd is far worse than none.
.PHONY: _rm-data-tree
_rm-data-tree:
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -z "$$root" ] || [ ! -d "$$root" ]; then \
		printf "  $(Y)skipped$(X) no data tree to remove\n"; \
	else \
		case "$$root" in /|/root|/home|/usr|/etc|/var|/boot|/data-root) \
			printf "  $(R)REFUSED$(X) NUS_VOLUME_ROOT is %s — too broad to delete\n" "$$root"; exit 1;; esac; \
		if [ "$(RM_OSM)" = 1 ]; then keep=""; else keep="nus-osm-data"; fi; \
		before=0; after=0; \
		for d in $$(find "$$root" -maxdepth 1 -mindepth 1 -type d -name 'nus-*' 2>/dev/null); do \
			[ -n "$$keep" ] && [ "$$(basename "$$d")" = "$$keep" ] && continue; \
			before=$$((before+1)); rm -rf "$$d" 2>/dev/null || true; \
			[ -d "$$d" ] && after=$$((after+1)) || true; done; \
		[ -z "$$keep" ] && rmdir "$$root" 2>/dev/null || true; \
		if [ "$$after" -eq 0 ]; then \
			printf "  $(G)removed$(X) %s data directories under %s\n" "$$before" "$$root"; \
			[ -n "$$keep" ] && [ -d "$$root/$$keep" ] && \
				printf "  $(G)kept$(X)    %s (%s) — the street map, so the next bootstrap need not refetch it\n" \
					"$$keep" "$$(du -sh "$$root/$$keep" 2>/dev/null | cut -f1)" || true; \
		else \
			printf "  $(R)FAIL$(X)    %s of %s data directories could NOT be removed under %s\n" "$$after" "$$before" "$$root"; \
			printf "              The containers write as their own users, so these files are\n"; \
			printf "              not yours to delete. Re-run as root:\n"; \
			printf "                $(C)sudo make $(MAKECMDGOALS)$(X)\n"; \
			printf "              $(Y)Until they are gone the next 'make up' will find them and treat\n"; \
			printf "              them as a fresh volume — a half-initialised cluster.$(X)\n"; \
			exit 1; \
		fi; \
	fi

.PHONY: clean-images
clean-images: ## Remove every image this stack builds, pulls, or builds FROM
	$(call say,Removing images)
	@built=$$($(COMPOSE) config --images 2>/dev/null | grep '^nus/' | sort -u); \
	pulled=$$($(COMPOSE) --profile init config --images 2>/dev/null | grep -v '^nus/' | sort -u); \
	bases=$$(grep -oE '^(PG|CONNECT|GRAFANA|SUPERSET|PYTHON|UV)_IMAGE=.+' $(ENV_FILE) 2>/dev/null | cut -d= -f2-); \
	for i in $$built $$pulled $$bases; do \
		if docker image inspect "$$i" >/dev/null 2>&1; then \
			docker rmi "$$i" >/dev/null 2>&1 \
				&& printf "  $(G)removed$(X) %s\n" "$$i" \
				|| printf "  $(Y)in use$(X)  %s (a container still references it)\n" "$$i"; \
		fi; \
	done
	@printf "  $(G)ok$(X)      build cache left alone; 'docker builder prune' clears that\n"

.PHONY: clean
clean: ## LEAVE NO TRACE: containers, volumes, images, network and local .env
	@printf "$(R)$(B)This removes everything this project put on the host:$(X)\n"
	@printf "  - every container, and the whole data tree under NUS_VOLUME_ROOT\n"
	@printf "    (databases, cache, topics, warehouse, and the downloaded street map)\n"
	@printf "  - every image it built and pulled\n"
	@printf "  - the $(NETWORK) network\n"
	@printf "  - your local .env, and the etcd state goes back to 'new'\n"
	@printf "\n  The repository itself is untouched, and nothing outside Docker is.\n\n"
	@read -r -p "Type 'clean' to confirm: " a; [ "$$a" = clean ] || { echo "cancelled"; exit 1; }
	@$(COMPOSE) --profile init down -v --remove-orphans 2>/dev/null || true
	$(call ok,containers and volume entries removed)
	@$(MAKE) --no-print-directory _rm-data-tree
	@$(MAKE) --no-print-directory clean-images
	@docker network rm $(NETWORK) >/dev/null 2>&1 && printf "  $(G)removed$(X) network $(NETWORK)\n" \
		|| printf "  $(Y)skipped$(X) network $(NETWORK) (already gone, or still in use)\n"
	@$(MAKE) --no-print-directory etcd-new >/dev/null
	$(call ok,etcd state back to 'new')
	@if [ -f $(ENV_FILE) ]; then mv $(ENV_FILE) $(ENV_FILE).removed; \
		printf "  $(G)moved$(X)   $(ENV_FILE) -> $(ENV_FILE).removed (your passwords, kept just in case)\n"; fi
	@printf "\n$(G)$(B)Clean.$(X) The host is as it was. 'make init' starts again from nothing.\n"
	@printf "  Docker's shared build cache is the one thing left: $(C)docker builder prune$(X)\n\n"
