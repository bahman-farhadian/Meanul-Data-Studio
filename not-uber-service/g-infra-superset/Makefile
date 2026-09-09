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

.PHONY: superset-health
superset-health: ## Superset answers on its health endpoint
	$(call say,Superset)
	@$(COMPOSE) exec -T superset curl -sS http://localhost:8088/health 2>/dev/null || true
	@printf "\n  Empty panels are fine before bootstrap; an error is not.\n"
