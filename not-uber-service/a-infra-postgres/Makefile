# a-infra-postgres — this piece's own operational targets.
#
# Included by the root Makefile (`include a-infra-postgres/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd a-infra-postgres && make`. What this component is
# and how to verify it: README.md.

# =============================================================================
##@ a-infra-postgres
# =============================================================================

.PHONY: certgen
certgen: ## Generate the etcd TLS material (skips itself once the certs exist)
	$(call say,etcd TLS certificates)
	@$(COMPOSE) run --rm etcd-certgen

.PHONY: etcd-existing
etcd-existing: ## Post-bootstrap: flip etcd to 'existing' (LOCAL STATE — never commit)
	$(call say,Flipping the etcd cluster state to existing)
	@sed -i 's/^ETCD_INITIAL_CLUSTER_STATE=new$$/ETCD_INITIAL_CLUSTER_STATE=existing/' $(ETCD_ENV)
	@grep -E '^ETCD_INITIAL_CLUSTER_STATE=' $(ETCD_ENV) | sed 's/^/  now: /'
	@$(COMPOSE) up -d --wait --wait-timeout 120 etcd-1 etcd-2 etcd-3
	$(call ok,etcd recreated; the data volumes were kept)
	@printf "  $(Y)Do not commit $(ETCD_ENV).$(X) A fresh clone must bootstrap from 'new';\n"
	@printf "  on a live cluster 'new' would let a member with a lost volume start its own.\n"

.PHONY: etcd-new
etcd-new: ## Put etcd back to 'new' (only before a fresh bootstrap from empty volumes)
	@sed -i 's/^ETCD_INITIAL_CLUSTER_STATE=existing$$/ETCD_INITIAL_CLUSTER_STATE=new/' $(ETCD_ENV)
	@grep -E '^ETCD_INITIAL_CLUSTER_STATE=' $(ETCD_ENV) | sed 's/^/  now: /'

.PHONY: pg-cpus-bootstrap
pg-cpus-bootstrap: ## Raise PG_CPUS to PG_CPUS_BOOTSTRAP for the routing burst (recreates pg-1/2/3)
	$(call say,Raising PG_CPUS for bootstrap's routing burst)
	@sed -i "s/^PG_CPUS=.*/PG_CPUS=$(call getenv,PG_CPUS_BOOTSTRAP,10)/" $(ENV_FILE)
	@grep -E '^PG_CPUS=' $(ENV_FILE) | sed 's/^/  now: /'
	@$(COMPOSE) up -d --wait --wait-timeout $(WAIT_TIMEOUT) pg-1 pg-2 pg-3
	$(call ok,pg-1/2/3 recreated with the higher ceiling)
	@$(MAKE) --no-print-directory _wait-haproxy-pg-settled

.PHONY: pg-cpus-normal
pg-cpus-normal: ## Put PG_CPUS back to its steady-state value (recreates pg-1/2/3)
	$(call say,Returning PG_CPUS to its steady-state value)
	@sed -i 's/^PG_CPUS=.*/PG_CPUS=2/' $(ENV_FILE)
	@grep -E '^PG_CPUS=' $(ENV_FILE) | sed 's/^/  now: /'
	@$(COMPOSE) up -d --wait --wait-timeout $(WAIT_TIMEOUT) pg-1 pg-2 pg-3
	$(call ok,pg-1/2/3 recreated at the steady-state ceiling)
	@$(MAKE) --no-print-directory _wait-haproxy-pg-settled

.PHONY: _wait-haproxy-pg-settled
_wait-haproxy-pg-settled: ## Wait past the post-recreation flap, not just pg-1/2/3's own healthcheck
	@printf "  waiting for HAProxy's own health checks to settle: recreating\n"
	@printf "  pg-1/2/3 together causes a burst of Docker DNS churn that can flap a\n"
	@printf "  backend down for a moment, and haproxy.cfg.template deliberately kills\n"
	@printf "  whatever is mid-query on a backend the instant it is marked down\n"
	@printf "  (default-server on-marked-down shutdown-sessions - correct behaviour\n"
	@printf "  for a real demotion, but it also fires on a transient flap). pg-1/2/3\n"
	@printf "  answering their own healthcheck does not mean HAProxy has caught up.\n"
	@stable=0; write_up=0; read_up=0; \
	for i in $$(seq 1 30); do \
		csv=$$(curl -fsS "http://localhost:$(call getenv,LB_A_STATS_PORT,8404)/stats;csv" 2>/dev/null || true); \
		write_up=$$(printf '%s\n' "$$csv" | awk -F, '$$1=="pg_write" && $$2 ~ /^nus-pg-/ && $$18=="UP"' | wc -l); \
		read_up=$$(printf '%s\n' "$$csv" | awk -F, '$$1=="pg_read" && $$2 ~ /^nus-pg-/ && $$18=="UP"' | wc -l); \
		if [ "$$write_up" = "1" ] && [ "$$read_up" = "2" ]; then \
			stable=$$((stable + 1)); \
		else \
			stable=0; \
		fi; \
		if [ "$$stable" -ge 3 ]; then \
			printf "  $(G)ok$(X)      HAProxy stable: pg_write 1 up, pg_read 2 up\n"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	printf "  $(R)FAIL$(X)    HAProxy never settled after 30s (last seen: pg_write=%s pg_read=%s)\n" "$$write_up" "$$read_up"; \
	printf "              check manually: make verify-pg\n"; \
	exit 1

.PHONY: verify-pg
verify-pg: ## etcd health, Patroni roles, and what the proxy routes where
	$(call say,PostgreSQL)
	@$(COMPOSE) exec -T etcd-1 etcdctl \
		--endpoints=https://nus-etcd-1:2379,https://nus-etcd-2:2379,https://nus-etcd-3:2379 \
		--cacert=/certs/ca.crt --cert=/certs/client.crt --key=/certs/client.key \
		endpoint health || true
	@echo
	@$(COMPOSE) exec -T pg-1 patronictl -c /etc/patroni/patroni.yml list || true
	@printf "\n  Expect exactly one Leader and two Replicas, with lag at or near 0.\n"
	@printf "  HAProxy view: http://localhost:$(call getenv,LB_A_STATS_PORT,8404)/stats\n"
	@printf "  Red rows there are $(B)expected$(X): the checks ask which ROLE a node holds,\n"
	@printf "  so pg_write shows 1 UP and pg_read shows 2 UP. A node down in BOTH is the\n"
	@printf "  only real failure signal.\n"

.PHONY: psql psql-read patronictl
psql: ## A psql shell on the current leader, through the proxy
	@$(COMPOSE) exec pg-1 psql "host=nus-lb-a port=5432 user=postgres password=$(call getenv,PG_SUPERUSER_PASSWORD) dbname=$(call getenv,PG_DATABASE,nus)"
psql-read: ## A psql shell on the replica pool, through the proxy
	@$(COMPOSE) exec pg-1 psql "host=nus-lb-a port=5433 user=postgres password=$(call getenv,PG_SUPERUSER_PASSWORD) dbname=$(call getenv,PG_DATABASE,nus)"
patronictl: ## patronictl, for switchover and friends:  make patronictl ARGS=list
	@$(COMPOSE) exec pg-1 patronictl -c /etc/patroni/patroni.yml $(or $(ARGS),list)

.PHONY: failover-pg
failover-pg: ## Demo: hand the PostgreSQL leadership to another node
	@$(COMPOSE) exec pg-1 patronictl -c /etc/patroni/patroni.yml switchover --force
	@printf "\n  HAProxy follows the promotion on its own. Watch: make verify-pg\n"
