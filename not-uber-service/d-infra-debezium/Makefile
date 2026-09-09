# d-infra-debezium — this piece's own operational targets.
#
# Included by the root Makefile (`include d-infra-debezium/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd d-infra-debezium && make`. What this component is
# and how to verify it: README.md.

# =============================================================================
##@ d-infra-debezium
# =============================================================================

.PHONY: cdc-register
cdc-register: ## Register the Debezium connector (only AFTER bootstrap made the tables)
	$(call say,Debezium connector)
	@$(COMPOSE) run --rm connector-register

.PHONY: verify-cdc
verify-cdc: ## Connector state, the cdc.* topics, and the replication slot
	$(call say,Debezium)
	@$(COMPOSE) exec -T debezium-connect curl -sS http://localhost:8083/connectors/nus-pg/status 2>/dev/null || true
	@echo
	@$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-1:9092 --list 2>/dev/null | grep '^cdc\.' || printf "  no cdc.* topics yet\n"
	@echo
	@$(COMPOSE) exec -T pg-1 psql -U postgres -c \
		"SELECT slot_name, active, wal_status FROM pg_replication_slots;" 2>/dev/null || true
	@printf "\n  The connector should be RUNNING and the slot active with wal_status 'reserved'.\n"
	@printf "  A slot that exists but is inactive makes PostgreSQL keep WAL for ever.\n"
