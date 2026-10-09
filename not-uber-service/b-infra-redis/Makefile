# b-infra-redis — this piece's own operational targets.
#
# Included by the root Makefile (`include b-infra-redis/Makefile`), so every
# target here runs from the repository root, against the shared master
# .env — never `cd b-infra-redis && make`. What this component is and how
# to verify it: README.md.

# =============================================================================
# Redis
# =============================================================================

.PHONY: verify-redis
verify-redis:
	$(call say,Redis)
	@$(COMPOSE) exec -T redis-1 redis-cli -a "$$(grep -E '^REDIS_PASSWORD=' $(ENV_FILE) | cut -d= -f2-)" --no-auth-warning ping
	@printf "\n  One Redis. A PONG is the check.\n"

.PHONY: redis-cli
redis-cli:
	@$(COMPOSE) exec redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning


