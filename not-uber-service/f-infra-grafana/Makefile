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
	@printf "\n  Empty panels are fine before bootstrap; an error is not.\n"
