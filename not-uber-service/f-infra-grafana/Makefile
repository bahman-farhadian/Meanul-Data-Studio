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
	base="http://127.0.0.1:$(call getenv,LB_A_GRAFANA_PORT,3000)"; \
	json_code=$$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$$base/tiles/styles.json" || true); \
	if [ "$$json_code" != 200 ]; then \
		printf "  $(R)FAIL$(X)    GET /tiles/styles.json -> %s (want 200). 503 means HAProxy has no tiles backend.\n" "$${json_code:-curl-failed}"; \
		exit 1; \
	fi; \
	printf "  $(G)ok$(X)      HAProxy /tiles/styles.json 200\n"; \
	out=$$(curl -sS -o /dev/null -w '%{http_code} %{content_type}' --max-time 60 "$$base/tiles/styles/nus/11/602/768.png" || true); \
	code=$${out%% *}; ctype=$${out#* }; \
	case "$$code:$$ctype" in \
		200:image/png*) printf "  $(G)ok$(X)      HAProxy /tiles/styles/nus/11/602/768.png 200 image/png\n" ;; \
		*) printf "  $(R)FAIL$(X)    GET /tiles/styles/nus/11/602/768.png -> %s (want 200 image/png)\n" "$$out"; exit 1 ;; \
	esac; \
	printf "  Grafana geomap is same-origin OSM, no MapTiler key.\n"

.PHONY: grafana-check
grafana-check: ## Dashboards query only ClickHouse Distributed tables
	@python3 f-infra-grafana/check-dashboards.py

# NYC OSM extract → OpenMapTiles MBTiles for tileserver-gl. Idempotent.
# The script lives in tiles/prepare.sh so a missing optional .env key
# cannot become a silent `pipefail` Error 1 (this Makefile uses bash -eu
# -o pipefail).
.PHONY: tiles-prepare
tiles-prepare: ## Build nyc.mbtiles (Planetiler) for the Grafana map
	$(call say,NYC OSM tiles)
	@bash f-infra-grafana/tiles/prepare.sh "$(ENV_FILE)"
