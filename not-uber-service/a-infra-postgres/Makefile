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
