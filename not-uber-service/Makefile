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
# ONE MAKEFILE PER COMPONENT, included below, exactly like the root
# docker-compose.yaml includes each component's own compose file. This file
# keeps only what is genuinely stack-wide: setup, the ordered bring-up,
# teardown, and diagnostics that cross more than one component. Everything
# specific to one piece — its own verify-*, a shell into it, a failover demo —
# lives in that piece's own Makefile and shows under its own heading in
# `make help`.
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

# Every component's own Makefile. Order follows the build order (README
# section 2.8.1) purely for readability — `include` does not care.
include a-infra-postgres/Makefile
include b-infra-redis/Makefile
include c-infra-kafka/Makefile
include d-infra-debezium/Makefile
include e-infra-clickhouse/Makefile
include f-infra-grafana/Makefile
include g-infra-superset/Makefile
include h-bootstrap/Makefile
include i-service-cache-updater/Makefile
include j-service-driver/Makefile
include k-service-passenger/Makefile
include l-service-dispatch/Makefile
include m-service-city/Makefile
include n-service-clickhouse-sink/Makefile

# How long to wait for a group of containers to report healthy. ClickHouse and
# Superset have a 40-60s start_period of their own, so this is generous.
WAIT_TIMEOUT ?= 420

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
PIECE_B  := redis-1 redis-2 redis-3 sentinel-1 sentinel-2 sentinel-3
PIECE_C  := kafka-1 kafka-2 kafka-3 schema-registry ksqldb-server
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
	@printf "\n$(B)not-uber-service$(X) — a cab platform's whole data lifecycle, in containers\n\n"
	@printf "  First deployment, in four commands:\n"
	@printf "    $(C)make init$(X)        create .env and the shared network\n"
	@printf "    $(C)\$$EDITOR .env$(X)     change every password in section 1\n"
	@printf "    $(C)make prepare$(X)     pull, build, fetch the map — the only step needing the net\n"
	@printf "    $(C)make up$(X)          the whole ordered bring-up\n\n"
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
# An existing .env only ever gains lines here — nothing already set is ever
# touched. Runs every time, on a brand-new .env and a years-old one alike:
# a var this template defines but an existing .env predates is exactly the
# error class that sent someone chasing "required variable ... is missing
# a value" through six unrelated targets instead of getting told once, at
# the one command meant to catch it. Idempotent — a second run finds
# nothing missing and says so.
	@missing=""; \
	for key in $$(grep -oE '^[A-Z_][A-Z0-9_]*=' $(EXAMPLE) | tr -d '='); do \
		grep -q "^$$key=" $(ENV_FILE) || { \
			grep -E "^$$key=" $(EXAMPLE) | tail -1 >> $(ENV_FILE); \
			missing="$$missing $$key"; \
		}; \
	done; \
	if [ -n "$$missing" ]; then \
		printf "  $(G)added$(X)   not in your $(ENV_FILE) yet, pulled in from $(EXAMPLE):%s\n" "$$missing"; \
	else \
		printf "  $(G)ok$(X)      every setting the template defines is already present\n"; \
	fi
# Ports and passwords get a plain change-me placeholder above, same as any
# other setting — preflight already refuses to deploy on one. These two are
# the exception: a guess beats a placeholder, because this is the one
# setting no proxy can paper over if it's wrong (README: c-infra-kafka).
# Only touches a value still literally the placeholder, so a real answer
# already in .env — yours, or detected on an earlier run — is never
# silently redetected and never drifts on its own.
	@if grep -qE '^KAFKA_ADVERTISED_HOST_A=change-me' $(ENV_FILE) 2>/dev/null \
		|| grep -qE '^KAFKA_ADVERTISED_HOST_B=change-me' $(ENV_FILE) 2>/dev/null; then \
		def=$$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'dev [a-z0-9.-]+' | awk '{print $$2}' | head -1 || true); \
		ips=$$(ip -4 -o addr show scope global 2>/dev/null \
			| awk '{gsub(/\/.*/,"",$$4); print $$2, $$4}' \
			| grep -vE '^(docker[0-9]*|br-[0-9a-f]+|veth[0-9a-f]*) ' || true); \
		a=$$(echo "$$ips" | awk -v d="$$def" '$$1==d{print $$2; f=1} END{if(!f) exit 1}' \
			|| echo "$$ips" | head -1 | awk '{print $$2}'); \
		b=$$(echo "$$ips" | awk -v skip="$$a" '$$2!=skip{print $$2; exit}'); \
		if grep -qE '^KAFKA_ADVERTISED_HOST_A=change-me' $(ENV_FILE) && [ -n "$$a" ]; then \
			sed -i "s/^KAFKA_ADVERTISED_HOST_A=.*/KAFKA_ADVERTISED_HOST_A=$$a/" $(ENV_FILE); \
			printf "  $(G)detected$(X) KAFKA_ADVERTISED_HOST_A=$$a\n"; \
		fi; \
		if grep -qE '^KAFKA_ADVERTISED_HOST_B=change-me' $(ENV_FILE); then \
			bb=$${b:-$$a}; \
			if [ -n "$$bb" ]; then \
				sed -i "s/^KAFKA_ADVERTISED_HOST_B=.*/KAFKA_ADVERTISED_HOST_B=$$bb/" $(ENV_FILE); \
				printf "  $(G)detected$(X) KAFKA_ADVERTISED_HOST_B=$$bb\n"; \
				[ -z "$$b" ] && printf "  $(Y)note$(X)    only one network path found — B set the same as A\n"; \
			else \
				printf "  $(Y)warn$(X)    no network interface found — edit KAFKA_ADVERTISED_HOST_A/B by hand\n"; \
			fi; \
		fi; \
		printf "  $(Y)Both are a guess from this host's current interfaces — confirm they are\n"; \
		printf "  actually how a client will reach this host before deploying piece c.$(X)\n"; \
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
	fi
	@$(MAKE) --no-print-directory _mk-data-tree
	@printf "\n  Now edit $(B)$(ENV_FILE)$(X) — section 1 holds every password.\n"
	@printf "  Then, in order:\n"
	@printf "    $(C)make prepare$(X)    pull, build and prepare the map (needs the internet)\n"
	@printf "    $(C)make up$(X)         the bring-up (needs no internet at all)\n\n"

# Create exactly the directories the compose model mounts. Both `init` and
# `up` call it: after `make destroy` the tree is gone, and requiring a manual
# `make init` in between would be a trap — the error would arrive from
# preflight, several steps away from the cause.
.PHONY: prepare
prepare: ## Pull, build and prepare the street map — the only step needing network access
	$(call say,Fetching everything the stack needs)
	@printf "  This is the only step that reaches outside this host. After it the\n"
	@printf "  stack is self-contained: the containers talk to each other on %s,\n" "$(NETWORK)"
	@printf "  and the one runtime download - the LION street map - is done here.\n\n"
	@$(MAKE) --no-print-directory pull
	@$(MAKE) --no-print-directory build
	@$(MAKE) --no-print-directory lion-fetch
	@$(MAKE) --no-print-directory tlc-zones-fetch
	@$(MAKE) --no-print-directory lion-prepare
	@printf "\n$(G)$(B)Ready.$(X) Run $(C)make up$(X) — it needs no network access.\n"
	@printf "  Every image is built and the routable graph is on disk, ready to restore.\n\n"

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
		         CH_PASSWORD GRAFANA_ADMIN_PASSWORD SUPERSET_ADMIN_PASSWORD SUPERSET_SECRET_KEY \
		         KAFKA_CLUSTER_ID KSQLDB_ADMIN_PASSWORD; do \
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
	missingimg=""; nimg=0; \
	for i in $$($(COMPOSE) --profile init config --images 2>/dev/null | sort -u || true); do \
		nimg=$$((nimg+1)); docker image inspect "$$i" >/dev/null 2>&1 || missingimg="$$missingimg $$i"; done; \
	graph=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-)/nus-lion-data/routable-graph.dump; \
	if [ "$$nimg" -gt 0 ] && [ -z "$$missingimg" ] && [ -s "$$graph" ]; then \
		printf "  $(G)ok$(X)      all %s images built and the routable graph is on disk\n" "$$nimg"; \
		printf "  $(G)ok$(X)      nothing left to do needs the internet\n"; \
	else \
		if [ -n "$$missingimg" ]; then \
			printf "  $(R)FAIL$(X)    %s image(s) not built or pulled yet:\n" "$$(echo $$missingimg | wc -w)"; \
			for m in $$missingimg; do printf "                %s\n" "$$m"; done; \
			printf "              Run $(C)make prepare$(X) — it pulls, builds and prepares the map.\n"; \
			printf "              That is the only step that needs the internet.\n"; \
			fail=1; \
		fi; \
		[ -s "$$graph" ] || printf "  $(Y)warn$(X)    the routable graph is not built yet — $(C)make lion-fetch && make lion-prepare$(X)\n"; \
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
			printf "              so the image builds will use the host's network and succeed. Nothing\n"; \
			printf "              else needs container-side internet - lion-fetch/lion-prepare both run\n"; \
			printf "              on the host, and bootstrap only ever restores what they built.\n"; \
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
			printf "              captures traffic originating on the host but not traffic forwarded\n"; \
			printf "              from containers.\n"; \
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
			printf "  $(R)FAIL$(X)    host ports held by something else:%s\n" "$$busy"; fail=1; \
			for p in $$busy; do \
				who=$$(ss -ltnp 2>/dev/null | grep -E "[:.]$$p[[:space:]]" \
					| grep -oE 'users:\(\("[^"]+"' | grep -oE '"[^"]+"' | tr -d '"' | sort -u | paste -sd, - || true); \
				ctr=$$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -E "[:.]$$p->" | cut -f1 | paste -sd, - || true); \
				if [ -n "$$ctr" ]; then label="container $$ctr"; \
				elif [ -n "$$who" ]; then label="host process $$who"; \
				else label="something this user cannot see — retry: sudo ss -ltnp | grep :$$p"; fi; \
				printf "            %-6s held by %s\n" "$$p" "$$label"; \
			done; \
			printf "            Either stop that, or change the LB_* port in $(ENV_FILE).\n"; \
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


.PHONY: _mk-data-tree
_mk-data-tree:
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	[ -n "$$root" ] || exit 0; \
	for d in $$($(COMPOSE) config 2>/dev/null | grep -oE 'device: .+' | cut -d' ' -f2- || true); do \
		mkdir -p "$$d"; done; \
	made=$$(find "$$root" -maxdepth 1 -mindepth 1 -type d -name 'nus-*' 2>/dev/null | wc -l); \
	free=$$(df -BG --output=avail "$$root" 2>/dev/null | tail -1 | tr -dc '0-9'); \
	printf "  $(G)ok$(X)      %s data directories under %s (%s GB free)\n" "$$made" "$$root" "$$free"

.PHONY: pull
pull: ## Pull every pinned image (a moved or withdrawn tag surfaces here)
	$(call say,Pulling pinned images)
	@$(COMPOSE) --profile init pull --ignore-buildable

.PHONY: build
build: ## Build the eleven images this repository defines
	$(call say,Building images)
	@$(COMPOSE) build

.PHONY: config
config: # Print the fully merged compose model (what Docker actually sees)
	@$(COMPOSE) config

# =============================================================================
##@ Deploy
# =============================================================================

.PHONY: up
up: ## THE FIRST DEPLOYMENT: every piece, in order, with the one-shots
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
	@$(MAKE) --no-print-directory ch-ddl
	@$(MAKE) --no-print-directory superset-init
	@$(MAKE) --no-print-directory bootstrap
	@$(MAKE) --no-print-directory cdc-register
	$(call say,Starting the six services — pieces i to n)
	@$(COMPOSE) up -d $(SERVICES)
	@printf "\n$(G)$(B)The stack is up.$(X)\n\n"
	@printf "  One step is left, and it is $(B)local state you must never commit$(X):\n"
	@printf "    $(C)make etcd-existing$(X)   flip the etcd cluster out of bootstrap mode\n\n"
	@printf "  Then: $(C)make verify$(X), $(C)make urls$(X), $(C)make lag$(X)\n\n"

.PHONY: up-piece
up-piece: ## One piece only:  make up-piece PIECE=a  (a-g, or infra, or services)
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
restart: ## Restart one service:  make restart SVC=driver-service
	@test -n "$(SVC)" || { printf "$(R)set SVC=<service>$(X)\n"; exit 1; }
	@$(COMPOSE) restart $(SVC)

# =============================================================================
##@ One-shots (idempotent — safe to re-run at any time)
# =============================================================================

.PHONY: volume-perms
volume-perms: ## Hand each data directory to the user that writes to it (BEFORE first start)
	$(call say,Data directory ownership)
	@$(COMPOSE) run --rm volume-perms

.PHONY: lb-config
lb-config: ## Render haproxy.cfg with REDIS_PASSWORD (BEFORE lb-a/lb-b first start)
	$(call say,Entry tier config)
	@$(COMPOSE) run --rm haproxy-config-render

# =============================================================================
##@ Verify
# =============================================================================

.PHONY: verify
verify: verify-pg verify-redis verify-kafka verify-ksqldb verify-cdc verify-ch verify-dash verify-data ## Every check below, in order

.PHONY: verify-dash
verify-dash: grafana-health superset-health ## Grafana and Superset answer, with their data sources registered

# =============================================================================
##@ Operate
# =============================================================================

.PHONY: ps
ps: ## What is running, and what state it is in
	@$(COMPOSE) ps

.PHONY: health
health: ## One line per container: health, restarts, and whether it was OOM-killed
	@printf "  %-22s %-12s %-9s %s\n" CONTAINER HEALTH RESTARTS OOMKILLED
	@$(COMPOSE) ps -aq | while read -r c; do \
		docker inspect -f '  {{printf "%-22s" (slice .Name 1)}} {{if .State.Health}}{{printf "%-12s" .State.Health.Status}}{{else}}{{printf "%-12s" .State.Status}}{{end}} {{printf "%-9d" .RestartCount}} {{.State.OOMKilled}}' "$$c" 2>/dev/null; \
	done

# A replica's WAL receiver can race the leader's own replication-slot
# creation on first bootstrap - a known Patroni timing window, not a stuck
# cluster, and it self-heals on Patroni's own retry. verify-pg (one Leader,
# two Replicas, lag 0) is the real signal for whether it actually got stuck.
.PHONY: errors
errors: ## Collect what actually went wrong: exit codes, OOM kills, healthcheck output, error logs
	$(call say,Errors across the stack)
	@found=0; \
	for c in $$($(COMPOSE) ps -aq); do \
		name=$$(docker inspect -f '{{slice .Name 1}}' "$$c" 2>/dev/null); \
		state=$$(docker inspect -f '{{.State.Status}}' "$$c" 2>/dev/null); \
		code=$$(docker inspect -f '{{.State.ExitCode}}' "$$c" 2>/dev/null); \
		hs=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$$c" 2>/dev/null); \
		oomed=$$(docker inspect -f '{{.State.OOMKilled}}' "$$c" 2>/dev/null); \
		restarts=$$(docker inspect -f '{{.RestartCount}}' "$$c" 2>/dev/null); \
		bad=""; \
		[ "$$state" = exited ] && [ "$$code" != 0 ] && bad="exited $$code"; \
		[ "$$hs" = unhealthy ] && bad="$$bad unhealthy"; \
		[ "$$state" = restarting ] && bad="$$bad restarting"; \
		[ "$$oomed" = true ] && bad="$$bad oom-killed"; \
		[ "$${restarts:-0}" -gt 3 ] && bad="$$bad restart-looping(x$$restarts)"; \
		logs=$$(docker logs --since $(ERRORS_WINDOW) "$$c" 2>&1 \
			| grep -E '"level": ?"(ERROR|CRITICAL)"|level=error|<Error>|ERROR:|FATAL:|\[error\]|Traceback \(most recent|^[A-Za-z_.]*(Error|Exception):' \
			| grep -Ev 'FATAL: +the database system is starting up' \
			| grep -Ev 'replication slot "[^"]+" does not exist' \
			| tail -6 || true); \
		[ -z "$$bad" ] && [ -z "$$logs" ] && continue; \
		found=1; \
		printf "\n  $(B)%s$(X)  $(R)%s$(X)\n" "$$name" "$${bad:-log errors only}"; \
		if [ "$$hs" = unhealthy ]; then \
			out=$$(docker inspect -f '{{if .State.Health}}{{range .State.Health.Log}}{{.Output}}{{end}}{{end}}' "$$c" 2>/dev/null | tail -3); \
			[ -n "$$out" ] && printf "    $(Y)healthcheck said:$(X)\n%s\n" "$$(echo "$$out" | sed 's/^/      /')"; \
		fi; \
		[ -n "$$logs" ] && printf "%s\n" "$$(echo "$$logs" | cut -c1-150 | sed 's/^/      /')"; \
	done; \
	if [ "$$found" = 0 ]; then \
		printf "  $(G)ok$(X)      nothing exited badly, nothing unhealthy, nothing OOM-killed, no errors in the logs\n"; \
	else \
		printf "\n  An OOM kill or a restart loop means that component's limit or the\n"; \
		printf "  pacing must come down. Full log for any of them: $(C)make logs SVC=<name>$(X)\n"; \
		printf "  Confirmed everything above is a known, harmless startup race (verify-*\n"; \
		printf "  clean, ps healthy)? $(C)make errors-ack$(X) clears it, so the next run only\n"; \
		printf "  reports what actually happens after that point.\n"; \
	fi
	@printf "\n"

.PHONY: errors-ack
errors-ack: ## Clear every container's log AFTER confirming the stack is healthy - errors reports fresh from here
	$(call say,Clearing log history)
	@for c in $$($(COMPOSE) ps -aq); do \
		name=$$(docker inspect -f '{{slice .Name 1}}' "$$c" 2>/dev/null); \
		path=$$(docker inspect -f '{{.LogPath}}' "$$c" 2>/dev/null); \
		if [ -z "$$path" ]; then \
			printf "  $(Y)skip$(X)    %s (no LogPath - not the json-file log driver?)\n" "$$name"; \
		elif [ -w "$$path" ] 2>/dev/null; then \
			: > "$$path" && printf "  $(G)cleared$(X)  %s\n" "$$name"; \
		else \
			printf "  $(Y)skip$(X)    %s (no permission - run as root)\n" "$$name"; \
		fi; \
	done
	@printf "\n  $(C)make errors$(X) now only reports what happens after this point - it is\n"
	@printf "  not a substitute for fixing anything, only for not re-reading the same\n"
	@printf "  known-good startup noise on every future check.\n\n"

.PHONY: stats
stats: ## Live memory and CPU against each container's limit
	@docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' $$($(COMPOSE) ps -q) 2>/dev/null || true

.PHONY: lag
lag: ## Consumer lag per group — the health signal for the whole pipeline
	$(call say,Consumer lag)
	@for g in $(GROUPS); do \
		printf "\n  $(B)%s$(X)\n" "$$g"; \
		$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
			--bootstrap-server nus-kafka-1:9092 --describe --group "$$g" 2>/dev/null \
			| awk 'NR==1 || $$6 != "-"' | sed 's/^/    /' || printf "    (no such group yet)\n"; \
	done
	@printf "\n  Lag that stays small, or returns to small after a burst, is healthy.\n"
	@printf "  Lag that only grows is not — turn the pacing down in $(ENV_FILE).\n"

.PHONY: profile
profile: ## Profile the generated data — every dimension, range and null rate
	$(call say,What the warehouse actually holds)
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" \
		--password "$(call getenv,CH_PASSWORD)" --database nus --multiquery \
		--output-format PrettyCompactMonoBlock < z-config/profile.sql
	$(call say,Kafka — how many messages each topic actually holds)
	@printf "  %-28s %10s %10s\n" TOPIC PARTITIONS MESSAGES
	@$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-get-offsets.sh \
		--bootstrap-server nus-kafka-1:9092 2>/dev/null \
		| awk -F: '$$1 !~ /^(__|_schemas|connect_)/ {parts[$$1]++; total[$$1]+=$$3} \
		           END{for (t in total) printf "  %-28s %10d %10d\n", t, parts[t], total[t]}' \
		| sort || true
	@printf "\n  A topic at 0 messages has never been written to. That is where a stalled\n"
	@printf "  pipeline shows itself — every consumer downstream of it will read 0 lag\n"
	@printf "  while doing nothing at all.\n\n"
	@for g in $(GROUPS); do \
		$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
			--bootstrap-server nus-kafka-1:9092 --describe --group "$$g" 2>/dev/null \
			| awk -v g="$$g" 'NR>1 && $$6 != "-" {lag+=$$6; n++} END{if(n) printf "  %-24s partitions=%d total_lag=%d\n", g, n, lag}' || true; \
	done
	$(call say,Redis — what the services put in the cache)
	@$(COMPOSE) exec -T redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning \
		--scan --count 1000 2>/dev/null \
		| sed -E 's/[0-9a-f-]{8,}.*//; s/[0-9]+$$//' | sort | uniq -c | sort -rn | head -20 \
		| awk '{printf "  %8s  %s*\n", $$1, $$2}' || true
	@printf "  bootstrap flag: %s\n" "$$($(COMPOSE) exec -T redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning get system:bootstrap:done 2>/dev/null | tr -d '\r')"

.PHONY: logs
logs: ## Follow logs:  make logs  |  make logs SVC=dispatch-service
	@if [ -n "$(SVC)" ]; then $(COMPOSE) logs -f --tail=200 $(SVC); else $(COMPOSE) logs -f --tail=50; fi

.PHONY: urls
urls: ## Where to point a browser or a client, from here and from the network
	@def=$$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'dev [a-z0-9.-]+' | awk '{print $$2}' | head -1 || true); \
	ips=$$(ip -4 -o addr show scope global 2>/dev/null \
		| awk '{gsub(/\/.*/,"",$$4); print $$2, $$4}' \
		| grep -vE '^(docker[0-9]*|br-[0-9a-f]+|veth[0-9a-f]*) ' || true); \
	printf "\n  The entry tier binds $(B)0.0.0.0$(X), so it answers on every address\n"; \
	printf "  this host has — pick whichever one you can actually reach from where\n"; \
	printf "  you are connecting:\n\n"; \
	if [ -z "$$ips" ]; then printf "    (none found — is this host on a network at all?)\n"; \
	else echo "$$ips" | while read -r ifc addr; do \
		mark=""; [ "$$ifc" = "$$def" ] && mark=" (default route)"; \
		printf "    %-16s %-16s%s\n" "$$addr" "$$ifc" "$$mark"; \
	done; fi; \
	printf "\n  Substitute one of those for $(C)<host>$(X) below — every port is the same\n"; \
	printf "  regardless of which address you reach it on.\n\n"; \
	printf "  %-18s %-26s %s\n" "" "via lb-a (canonical)" "via lb-b (failover twin)"; \
	printf "  %-18s %-26s %s\n" "PostgreSQL writes" "<host>:$(call getenv,LB_A_PG_WRITE_PORT,5432)" "<host>:$(call getenv,LB_B_PG_WRITE_PORT,15432)"; \
	printf "  %-18s %-26s %s\n" "PostgreSQL reads"  "<host>:$(call getenv,LB_A_PG_READ_PORT,5433)"  "<host>:$(call getenv,LB_B_PG_READ_PORT,15433)"; \
	printf "  %-18s %-26s %s\n" "Redis writes"      "<host>:$(call getenv,LB_A_REDIS_WRITE_PORT,6379)" "<host>:$(call getenv,LB_B_REDIS_WRITE_PORT,16379)"; \
	printf "  %-18s %-26s %s\n" "Redis reads"       "<host>:$(call getenv,LB_A_REDIS_READ_PORT,6380)"  "<host>:$(call getenv,LB_B_REDIS_READ_PORT,16380)"; \
	printf "  %-18s %-26s %s\n" "ksqlDB"            "http://<host>:$(call getenv,LB_A_KSQLDB_PORT,8089)" "http://<host>:$(call getenv,LB_B_KSQLDB_PORT,18089)"; \
	printf "  %-18s %-26s %s\n" "Debezium Connect"  "http://<host>:$(call getenv,LB_A_DEBEZIUM_PORT,8083)" "http://<host>:$(call getenv,LB_B_DEBEZIUM_PORT,18083)"; \
	printf "  %-18s %-26s %s\n" "ClickHouse HTTP"   "<host>:$(call getenv,LB_A_CH_HTTP_PORT,8123)"  "<host>:$(call getenv,LB_B_CH_HTTP_PORT,18123)"; \
	printf "  %-18s %-26s %s\n" "ClickHouse native" "<host>:$(call getenv,LB_A_CH_NATIVE_PORT,9000)" "<host>:$(call getenv,LB_B_CH_NATIVE_PORT,19000)"; \
	printf "  %-18s %-26s %s\n" "Grafana"           "http://<host>:$(call getenv,LB_A_GRAFANA_PORT,3000)"  "http://<host>:$(call getenv,LB_B_GRAFANA_PORT,13000)"; \
	printf "  %-18s %-26s %s\n" "Superset"          "http://<host>:$(call getenv,LB_A_SUPERSET_PORT,8088)" "http://<host>:$(call getenv,LB_B_SUPERSET_PORT,18088)"; \
	printf "  %-18s %-26s %s\n" "HAProxy stats"     "http://<host>:$(call getenv,LB_A_STATS_PORT,8404)/stats" "http://<host>:$(call getenv,LB_B_STATS_PORT,18404)/stats"; \
	printf "\n  A SQL client such as DBeaver connects straight to the PostgreSQL and\n"; \
	printf "  ClickHouse addresses above — no extra proxy is needed. For Kafka, point\n"; \
	printf "  DBeaver at the ksqlDB address instead: its Kafka support only speaks\n"; \
	printf "  ksqlDB's REST API, not the raw broker protocol below. It asks for a\n"; \
	printf "  login — $(C)KSQLDB_ADMIN_USER$(X)/$(C)_PASSWORD$(X) in $(ENV_FILE), Kafka itself has none.\n"; \
	printf "  To publish on one interface only, set the LB_* ports in $(ENV_FILE)\n"; \
	printf "  to a bound form such as $(C)192.168.8.3:5432$(X).\n\n"; \
	printf "  The six ports below are different: a raw Kafka client (kcat, a\n"; \
	printf "  producer/consumer library) bootstraps here and gets handed one fixed\n"; \
	printf "  address per broker, not $(B)<host>$(X) — set by $(C)KAFKA_ADVERTISED_HOST_A$(X)/$(C)_B$(X)\n"; \
	printf "  in $(ENV_FILE). Bootstrap on any one; a client resolves the rest from there.\n\n"; \
	printf "  %-10s %-24s %s\n" "" "via address A" "via address B"; \
	printf "  %-10s %-24s %s\n" "kafka-1" "$(call getenv,KAFKA_ADVERTISED_HOST_A):9094" "$(call getenv,KAFKA_ADVERTISED_HOST_B):9097"; \
	printf "  %-10s %-24s %s\n" "kafka-2" "$(call getenv,KAFKA_ADVERTISED_HOST_A):9095" "$(call getenv,KAFKA_ADVERTISED_HOST_B):9098"; \
	printf "  %-10s %-24s %s\n" "kafka-3" "$(call getenv,KAFKA_ADVERTISED_HOST_A):9096" "$(call getenv,KAFKA_ADVERTISED_HOST_B):9099"; \
	printf "\n"

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
	@$(MAKE) --no-print-directory _rm-data-tree RM_MAP=1
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
		if [ "$(RM_MAP)" = 1 ]; then keep=""; else keep="nus-lion-data"; fi; \
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
