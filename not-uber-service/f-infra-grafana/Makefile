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
	@printf "\n  Provisioned dashboards live under Connections → Dashboards → NUS.\n"

.PHONY: grafana-check
grafana-check: ## Dashboards query only ClickHouse Distributed tables
	@python3 f-infra-grafana/check-dashboards.py

# NYC OSM extract → OpenMapTiles MBTiles for tileserver-gl. Idempotent:
# skips when nyc.mbtiles is already on disk. Needs the internet once.
.PHONY: tiles-prepare
tiles-prepare: ## Build nyc.mbtiles (Planetiler) for the Grafana map
	$(call say,NYC OSM tiles)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	[ -n "$$root" ] || { printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT is not set\n"; exit 1; }; \
	mkdir -p "$$root/nus-tiles-data"; \
	if [ -s "$$root/nus-tiles-data/nyc.mbtiles" ]; then \
		printf "  skipped  $$root/nus-tiles-data/nyc.mbtiles already exists\n"; \
	else \
		printf "  running  Planetiler --area=new-york (one-time, needs the internet)\n"; \
		img=$$(grep -E '^PLANETILER_IMAGE=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
		[ -n "$$img" ] || img=ghcr.io/onthegomap/planetiler:0.8.4; \
		docker run --rm \
			-e JAVA_TOOL_OPTIONS="-Xmx16g" \
			-v "$$root/nus-tiles-data:/data" \
			"$$img" \
			--download --area=new-york --output=/data/nyc.mbtiles; \
		[ -s "$$root/nus-tiles-data/nyc.mbtiles" ] \
			|| { printf "  $(R)FAIL$(X)    planetiler did not write nyc.mbtiles\n"; exit 1; }; \
		printf "  wrote    $$root/nus-tiles-data/nyc.mbtiles\n"; \
	fi
