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

# NYC OSM extract → OpenMapTiles MBTiles for tileserver-gl. Idempotent.
# The script lives in tiles/prepare.sh so a missing optional .env key
# cannot become a silent `pipefail` Error 1 (this Makefile uses bash -eu
# -o pipefail).
.PHONY: tiles-prepare
tiles-prepare: ## Build nyc.mbtiles (Planetiler) for the Grafana map
	$(call say,NYC OSM tiles)
	@bash f-infra-grafana/tiles/prepare.sh "$(ENV_FILE)"
