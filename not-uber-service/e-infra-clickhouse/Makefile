# e-infra-clickhouse — this piece's own operational targets.
#
# Included by the root Makefile (`include e-infra-clickhouse/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd e-infra-clickhouse && make`. What this component
# is and how to verify it: README.md.

# =============================================================================
##@ e-infra-clickhouse
# =============================================================================

.PHONY: ch-secrets
ch-secrets: ## Write the ClickHouse inter-node login (BEFORE the nodes start)
	$(call say,ClickHouse cluster credentials)
	@$(COMPOSE) run --rm ch-secrets-init

.PHONY: ch-ddl
ch-ddl: ## Create the ClickHouse tables (after the four nodes are healthy)
	$(call say,ClickHouse tables)
	@$(COMPOSE) run --rm ch-ddl-init

.PHONY: verify-ch
verify-ch: ## Cluster members, Keeper, the tables, and replication delay
	$(call say,ClickHouse)
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT host_name, shard_num, replica_num FROM system.clusters WHERE cluster='nus_cluster' FORMAT PrettyCompact" 2>/dev/null || true
	@echo
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT database, name, engine FROM system.tables WHERE database='nus' ORDER BY name FORMAT PrettyCompact" 2>/dev/null || true
	@echo
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT table, absolute_delay FROM system.replicas FORMAT PrettyCompact" 2>/dev/null || true
	@printf "\n  Four members, the tables present, and absolute_delay at or near 0.\n"

.PHONY: ch-client
ch-client: ## A clickhouse-client shell
	@$(COMPOSE) exec ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" --database nus
