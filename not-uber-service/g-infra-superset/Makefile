# g-infra-superset — this piece's own operational targets.
#
# Included by the root Makefile (`include g-infra-superset/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd g-infra-superset && make`. What this component is
# and how to verify it: README.md.

# =============================================================================
# Superset
# =============================================================================

.PHONY: superset-init
superset-init:
	$(call say,Superset initialisation)
	@$(COMPOSE) run --rm superset-init

.PHONY: superset-assets
superset-assets:
	$(call say,Superset assets)
	@python3 g-infra-superset/render_assets.py
	@python3 g-infra-superset/check-assets.py

.PHONY: superset-health
superset-health:
	$(call say,Superset)
	@$(COMPOSE) exec -T superset curl -sS http://localhost:8088/health 2>/dev/null || true
	@printf "\n  Empty panels are fine before bootstrap; an error is not.\n"

.PHONY: verify-superset
verify-superset:
	$(call say,Superset assets)
	@$(COMPOSE) run --rm superset-verify
