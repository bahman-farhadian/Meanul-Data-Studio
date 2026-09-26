# g-infra-superset — this piece's own operational targets.
#
# Included by the root Makefile (`include g-infra-superset/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd g-infra-superset && make`. What this component is
# and how to verify it: README.md.

# =============================================================================
##@ g-infra-superset
# =============================================================================

.PHONY: superset-init
superset-init: ## Superset's own tables, admin user, roles and ClickHouse connection
	$(call say,Superset initialisation)
	@$(COMPOSE) run --rm superset-init

.PHONY: superset-assets
superset-assets: ## Re-write assets/ from render_assets.py, then validate it (local, no stack)
	$(call say,Superset assets)
	@python3 g-infra-superset/render_assets.py
	@python3 g-infra-superset/check-assets.py

.PHONY: superset-import
superset-import: ## Re-import the datasets, charts and dashboard onto a running Superset
	$(call say,Superset asset import)
	@$(COMPOSE) run --rm superset-init

.PHONY: superset-health
superset-health: ## Superset answers on its health endpoint
	$(call say,Superset)
	@$(COMPOSE) exec -T superset curl -sS http://localhost:8088/health 2>/dev/null || true
	@printf "\n  Empty panels are fine before bootstrap; an error is not.\n"

.PHONY: verify-superset
verify-superset: ## Every declared asset is imported, and every metric runs on real data
	$(call say,Superset assets)
	@$(COMPOSE) run --rm superset-verify
