# c-infra-kafka — this piece's own operational targets.
#
# Included by the root Makefile (`include c-infra-kafka/Makefile`), so every
# target here runs from the repository root, against the shared master
# .env — never `cd c-infra-kafka && make`. What this component is and how
# to verify it: README.md.

# =============================================================================
##@ c-infra-kafka
# =============================================================================

.PHONY: kafka-dirs
kafka-dirs: ## Hand the Kafka volumes to the user inside the image (covered by volume-perms)
	$(call say,Kafka data directory ownership)
	@$(COMPOSE) run --rm kafka-dirs

.PHONY: ksqldb-secrets
ksqldb-secrets: ## Write ksqlDB's Basic-auth credentials (BEFORE ksqldb-server's first start)
	$(call say,ksqlDB credentials)
	@$(COMPOSE) run --rm ksqldb-secrets-init

.PHONY: topics
topics: ## Create the Kafka topics from topics.tsv (after the brokers are healthy)
	$(call say,Kafka topics)
	@$(COMPOSE) run --rm kafka-topics-init

.PHONY: verify-kafka
verify-kafka: ## Brokers, the controller quorum, and every topic's replication
	$(call say,Kafka)
	@$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-broker-api-versions.sh \
		--bootstrap-server nus-kafka-1:9092 2>/dev/null | grep -E '^nus-kafka-[0-9]' || true
	@echo
	@$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server nus-kafka-1:9092 --describe 2>/dev/null | grep -E 'Topic:' || true
	@printf "\n  Every partition should list three replicas and three in-sync.\n"

.PHONY: ksql-ddl
ksql-ddl: ## Register every ksqlDB stream and table from ksql/*.sql (after the topics exist)
	$(call say,ksqlDB streams and tables)
	@$(COMPOSE) run --rm ksql-init

# Runs through ksql-init rather than ksqldb-server: the check reads the
# declared names out of ksql/*.sql, and ksql-init is the container that has
# that directory mounted. The server holds the answer, not the question.
.PHONY: verify-ksqldb
verify-ksqldb: ## Every declared stream and table is registered, and every query is RUNNING
	$(call say,ksqlDB)
	@$(COMPOSE) run --rm --entrypoint python3 ksql-init /ksql/check-ksql.py
