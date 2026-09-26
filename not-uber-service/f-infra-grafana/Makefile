# f-infra-grafana — this piece's own operational targets.
#
# Included by the root Makefile (`include f-infra-grafana/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd f-infra-grafana && make`. What this component is
# and how to verify it: README.md.

# =============================================================================
##@ f-infra-grafana
# =============================================================================

.PHONY: grafana-health
grafana-health: ## Grafana answers on its health endpoint
	$(call say,Grafana)
	@$(COMPOSE) exec -T grafana wget -qO- http://localhost:3000/api/health 2>/dev/null || true
	@printf "\n  Provisioned dashboards live under Dashboards → NUS.\n"
	@printf "  Geomap tiles are same-origin /tiles/ — checked by $(C)make tiles-health$(X).\n"

# The HTTP half runs through HAProxy from inside nus-backbone, never from a
# host address. It used to curl 127.0.0.1:LB_A_GRAFANA_PORT and reported a
# tiles failure on a stack whose tiles were perfectly healthy: Docker's
# published-port DNAT cannot send a loopback destination out to a real
# interface while net.ipv4.conf.*.route_localnet is 0, so docker-proxy
# accepts the connection and closes it - an empty reply, not the 503 this
# check's own message predicted. Confirmed on Dionysus: 127.0.0.1 empty,
# while ::1, the LAN address, the container IP and in-network all return
# 200 through the same HAProxy. Every sibling check already goes through a
# container for this reason; make urls is where host addresses belong, and
# it lists the real ones and never offers loopback.
.PHONY: tiles-health
tiles-health: ## OSM tiles answer through HAProxy (Grafana's map origin)
	$(call say,Tiles)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	mb="$$root/nus-tiles-data/nyc.mbtiles"; \
	if [ ! -s "$$mb" ]; then \
		printf "  $(R)FAIL$(X)    %s missing — run $(C)make tiles-prepare$(X) once\n" "$$mb"; \
		exit 1; \
	fi; \
	printf "  $(G)ok$(X)      nyc.mbtiles %s\n" "$$(du -h "$$mb" | cut -f1)"; \
	hs=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' nus-tiles 2>/dev/null || true); \
	if [ "$$hs" != healthy ]; then \
		printf "  $(R)FAIL$(X)    nus-tiles is '%s' — Grafana maps 503 until: $(C)docker compose up -d tiles$(X)\n" "$${hs:-not running}"; \
		exit 1; \
	fi; \
	printf "  $(G)ok$(X)      nus-tiles healthy\n"; \
	base="http://nus-lb-a:3000"; \
	json=$$($(COMPOSE) exec -T grafana wget -S -O /dev/null "$$base/tiles/styles.json" 2>&1 || true); \
	if ! printf '%s' "$$json" | grep -q ' 200 OK'; then \
		printf "  $(R)FAIL$(X)    GET /tiles/styles.json did not return 200. 503 means HAProxy has no tiles backend.\n"; \
		printf '%s\n' "$$json" | sed -n '1,6p' | sed 's/^/              /'; \
		exit 1; \
	fi; \
	printf "  $(G)ok$(X)      HAProxy /tiles/styles.json 200\n"; \
	png=$$($(COMPOSE) exec -T grafana wget -S -O /dev/null "$$base/tiles/styles/nus/11/602/768.png" 2>&1 || true); \
	if printf '%s' "$$png" | grep -q ' 200 OK' && printf '%s' "$$png" | grep -qi 'Content-Type: *image/png'; then \
		printf "  $(G)ok$(X)      HAProxy /tiles/styles/nus/11/602/768.png 200 image/png\n"; \
	else \
		printf "  $(R)FAIL$(X)    GET /tiles/styles/nus/11/602/768.png did not return 200 image/png\n"; \
		printf '%s\n' "$$png" | sed -n '1,6p' | sed 's/^/              /'; \
		exit 1; \
	fi; \
	printf "  Grafana geomap is same-origin OSM, no MapTiler key.\n"

.PHONY: grafana-check
grafana-check: ## Dashboards query only ClickHouse Distributed tables
	@python3 f-infra-grafana/check-dashboards.py

# The counterpart of verify-superset, and for the same reason: grafana-check
# proves a panel's SQL is well-formed and reads the right tables, but not
# that it RUNS. A renamed column or a function this ClickHouse version does
# not have is a panel that renders an error to whoever opens it first.
#
# stderr is folded into the captured output on purpose. clickhouse-client
# stops at the first bad statement and exits non-zero; without 2>&1 and the
# `|| true` the recipe would die before printing anything, which is exactly
# how `make capacity` once failed with Error 1 and no output at all.
.PHONY: verify-grafana
verify-grafana: ## Every provisioned panel's query, run against the live warehouse
	$(call say,Grafana panels)
	@out=$$(python3 f-infra-grafana/panel-probe.py \
		| $(COMPOSE) exec -T ch-s1r1 clickhouse-client \
			--user "$(call getenv,CH_USER)" \
			--password "$(call getenv,CH_PASSWORD)" \
			--database nus --multiquery 2>&1 || true); \
	printf '%s\n' "$$out"; \
	if printf '%s' "$$out" | grep -qE 'FAIL|DB::Exception'; then \
		printf "\n  $(R)A panel did not run, or returned nothing.$(X)\n"; \
		printf "  clickhouse-client stops at the first bad statement, so the\n"; \
		printf "  last panel named above is the one to look at.\n"; \
		exit 1; \
	fi; \
	printf "\n  $(G)Every panel ran and returned rows.$(X)\n"; \
	printf "  This does not prove a panel RENDERS - a wrong field name in an\n"; \
	printf "  override still needs eyes on the dashboard.\n"

# NYC OSM extract → OpenMapTiles MBTiles for tileserver-gl. Idempotent.
# The script lives in tiles/prepare.sh so a missing optional .env key
# cannot become a silent `pipefail` Error 1 (this Makefile uses bash -eu
# -o pipefail).
.PHONY: tiles-prepare
tiles-prepare: ## Build nyc.mbtiles (Planetiler) for the Grafana map
	$(call say,NYC OSM tiles)
	@bash f-infra-grafana/tiles/prepare.sh "$(ENV_FILE)"
