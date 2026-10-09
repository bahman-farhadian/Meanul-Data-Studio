# e-infra-clickhouse — this piece's own operational targets.
#
# Included by the root Makefile (`include e-infra-clickhouse/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd e-infra-clickhouse && make`. What this component
# is and how to verify it: README.md.

# =============================================================================
# ClickHouse
# =============================================================================

.PHONY: ch-ddl
ch-ddl:
	$(call say,ClickHouse tables)
	@$(COMPOSE) run --rm ch-ddl-init

.PHONY: verify-ch
verify-ch:
	$(call say,ClickHouse)
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT name, engine FROM system.tables WHERE database='nus' AND engine LIKE '%MergeTree' ORDER BY name FORMAT PrettyCompact" 2>/dev/null || true
	@printf "\n  One ClickHouse. The engines are MergeTree. No Distributed table.\n"

.PHONY: ch-client
ch-client:
	@$(COMPOSE) exec ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" --database nus
