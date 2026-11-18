# a-infra-postgres — this piece's own operational targets.
#
# Included by the root Makefile (`include a-infra-postgres/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd a-infra-postgres && make`. What this component is
# and how to verify it: README.md.

# =============================================================================
# Postgres
# =============================================================================

.PHONY: pg-cpus-bootstrap
pg-cpus-bootstrap:
	$(call say,Raising PG_CPUS for bootstrap's routing burst)
	@sed -i "s/^PG_CPUS=.*/PG_CPUS=$(call getenv,PG_CPUS_BOOTSTRAP,10)/" $(ENV_FILE)
	@grep -E '^PG_CPUS=' $(ENV_FILE) | sed 's/^/  now: /'
	@$(COMPOSE) up -d --wait --wait-timeout $(WAIT_TIMEOUT) hailpath-postgres
	$(call ok,hailpath-postgres recreated with the higher ceiling)

.PHONY: pg-cpus-normal
pg-cpus-normal:
	$(call say,Returning PG_CPUS to its steady-state value)
	@sed -i 's/^PG_CPUS=.*/PG_CPUS=$(call getenv,PG_CPUS_STEADY,10)/' $(ENV_FILE)
	@grep -E '^PG_CPUS=' $(ENV_FILE) | sed 's/^/  now: /'
	@$(COMPOSE) up -d --wait --wait-timeout $(WAIT_TIMEOUT) hailpath-postgres
	$(call ok,hailpath-postgres recreated at the steady-state ceiling)

.PHONY: pg-allow-cdc
pg-allow-cdc:
	$(call say,PostgreSQL pg_hba: replication for Debezium)
	@row=$$($(COMPOSE) exec -T hailpath-postgres psql -U postgres -d postgres -tA -c \
		"SELECT count(*) FROM pg_hba_file_rules WHERE 'replication' = ANY(database) AND 'postgres' = ANY(user_name)"); \
	if [ "$$row" = "0" ] || [ -z "$$row" ]; then \
		printf "  $(R)FAIL$(X)    pg_hba has no replication line for postgres\n"; \
		exit 1; \
	fi
	$(call ok,pg_hba allows the postgres user to open a logical replication slot)

.PHONY: verify-pg
verify-pg:
	$(call say,PostgreSQL)
	@$(COMPOSE) exec -T hailpath-postgres pg_isready -U postgres -d "$(call getenv,PG_DATABASE,hailpath)"
	@printf "\n  One PostgreSQL. Debezium reads this same database.\n"

.PHONY: psql
psql:
	@$(COMPOSE) exec hailpath-postgres psql "host=hailpath-postgres port=5432 user=postgres password=$(call getenv,PG_SUPERUSER_PASSWORD) dbname=$(call getenv,PG_DATABASE,hailpath)"
