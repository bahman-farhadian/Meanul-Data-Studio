# b-infra-redis — this piece's own operational targets.
#
# Included by the root Makefile (`include b-infra-redis/Makefile`), so every
# target here runs from the repository root, against the shared master
# .env — never `cd b-infra-redis && make`. What this component is and how
# to verify it: README.md.

# =============================================================================
##@ b-infra-redis
# =============================================================================

.PHONY: verify-redis
verify-redis: ## Node roles, what Sentinel believes, and whether it could fail over
	$(call say,Redis)
	@for n in redis-1 redis-2 redis-3; do \
		printf "  %-9s %s\n" "$$n" "$$($(COMPOSE) exec -T $$n redis-cli -a "$$(grep -E '^REDIS_PASSWORD=' $(ENV_FILE) | cut -d= -f2-)" --no-auth-warning info replication 2>/dev/null | awk -F: '/^role:/{print $$2}' | tr -d '\r')"; \
	done
	@echo
	@$(COMPOSE) exec -T sentinel-1 redis-cli -p 26379 sentinel ckquorum nus-cache || true
	@printf "\n  Expect one master, two slaves, and a quorum that can be reached.\n"

.PHONY: redis-cli
redis-cli: ## A redis-cli shell on the current primary
	@$(COMPOSE) exec redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning

.PHONY: failover-redis
failover-redis: ## Demo: ask Sentinel to promote a Redis replica
	@$(COMPOSE) exec sentinel-1 redis-cli -p 26379 sentinel failover nus-cache
	@printf "\n  Watch it land: make verify-redis\n"
