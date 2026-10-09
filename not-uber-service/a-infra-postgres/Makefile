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
	@$(COMPOSE) up -d --wait --wait-timeout $(WAIT_TIMEOUT) nus-postgres
	$(call ok,nus-postgres recreated with the higher ceiling)
	@$(MAKE) --no-print-directory _wait-haproxy-pg-settled

.PHONY: pg-cpus-normal
pg-cpus-normal:
	$(call say,Returning PG_CPUS to its steady-state value)
	@sed -i 's/^PG_CPUS=.*/PG_CPUS=$(call getenv,PG_CPUS_STEADY,10)/' $(ENV_FILE)
	@grep -E '^PG_CPUS=' $(ENV_FILE) | sed 's/^/  now: /'
	@$(COMPOSE) up -d --wait --wait-timeout $(WAIT_TIMEOUT) nus-postgres
	$(call ok,nus-postgres recreated at the steady-state ceiling)
	@$(MAKE) --no-print-directory _wait-haproxy-pg-settled

.PHONY: _wait-haproxy-pg-settled
_wait-haproxy-pg-settled:
	@printf "  waiting for HAProxy's health checks to settle after nus-postgres is recreated\n"
# The stats page is read through nus-backbone, not a host address. A host
# loopback read fails when localhost is 127.0.0.1 and route_localnet is 0.
	@stable=0; write_up=0; read_up=0; \
	for i in $$(seq 1 30); do \
		csv=$$($(COMPOSE) exec -T grafana wget -qO- "http://nus-lb-a:8404/stats;csv" 2>/dev/null || true); \
		write_up=$$(printf '%s\n' "$$csv" | awk -F, '$$1=="pg_write" && $$2 ~ /^nus-pg-/ && $$18=="UP"' | wc -l); \
		read_up=$$(printf '%s\n' "$$csv" | awk -F, '$$1=="pg_read" && $$2 ~ /^nus-pg-/ && $$18=="UP"' | wc -l); \
		if [ "$$write_up" = "1" ] && [ "$$read_up" = "1" ]; then \
			stable=$$((stable + 1)); \
		else \
			stable=0; \
		fi; \
		if [ "$$stable" -ge 3 ]; then \
			printf "  $(G)ok$(X)      HAProxy stable: pg_write 1 up, pg_read 1 up\n"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	printf "  $(R)FAIL$(X)    HAProxy never settled after 30s (last seen: pg_write=%s pg_read=%s)\n" "$$write_up" "$$read_up"; \
	printf "              check manually: make verify-pg\n"; \
	exit 1

.PHONY: pg-allow-cdc
pg-allow-cdc:
	$(call say,PostgreSQL pg_hba: replication for Debezium)
	@row=$$($(COMPOSE) exec -T nus-postgres psql -U postgres -d postgres -tA -c \
		"SELECT count(*) FROM pg_hba_file_rules WHERE 'replication' = ANY(database) AND 'postgres' = ANY(user_name)"); \
	if [ "$$row" = "0" ] || [ -z "$$row" ]; then \
		printf "  $(R)FAIL$(X)    pg_hba has no replication line for postgres\n"; \
		exit 1; \
	fi
	$(call ok,pg_hba allows the postgres user to open a logical replication slot)

.PHONY: verify-pg
verify-pg:
	$(call say,PostgreSQL)
	@$(COMPOSE) exec -T nus-postgres pg_isready -U postgres -d "$(call getenv,PG_DATABASE,nus)"
	@printf "\n  One PostgreSQL. Debezium reads this same database.\n"

.PHONY: psql psql-read
psql:
	@$(COMPOSE) exec nus-postgres psql "host=nus-lb-a port=5432 user=postgres password=$(call getenv,PG_SUPERUSER_PASSWORD) dbname=$(call getenv,PG_DATABASE,nus)"
psql-read:
	@$(COMPOSE) exec nus-postgres psql "host=nus-lb-a port=5433 user=postgres password=$(call getenv,PG_SUPERUSER_PASSWORD) dbname=$(call getenv,PG_DATABASE,nus)"
