# c-infra-kafka — this piece's own operational targets.
#
# Included by the root Makefile (`include c-infra-kafka/Makefile`), so every
# target here runs from the repository root, against the shared master
# .env — never `cd c-infra-kafka && make`. What this component is and how
# to verify it: README.md.

# =============================================================================
# Kafka
# =============================================================================

.PHONY: ksqldb-secrets
ksqldb-secrets:
	$(call say,ksqlDB credentials)
	@$(COMPOSE) run --rm ksqldb-secrets-init

.PHONY: topics
topics:
	$(call say,Kafka topics)
	@$(COMPOSE) run --rm kafka-topics-init

.PHONY: verify-kafka
verify-kafka:
	$(call say,Kafka)
	@$(COMPOSE) exec -T hailpath-kafka /opt/kafka/bin/kafka-broker-api-versions.sh \
		--bootstrap-server hailpath-kafka:9092 2>/dev/null | grep -E '^hailpath-kafka' || true
	@echo
	@$(COMPOSE) exec -T hailpath-kafka /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server hailpath-kafka:9092 --describe 2>/dev/null | grep -E 'Topic:' || true
	@printf "\n  Every partition has one replica. That replica is in sync.\n"

.PHONY: schemas
schemas:
	$(call say,Avro schemas)
	@$(COMPOSE) run --rm schema-init

# The expected subjects are read out of schemas/*.avsc. schema-init is the
# container with that directory mounted, and the Registry image's python3 is
# the only interpreter here that can reach :8081 - the port is not published
# through hailpath-proxy, and the image itself ships neither curl nor wget.
# Same arrangement verify-ksqldb uses for ksql-init.
.PHONY: verify-schemas
verify-schemas:
	$(call say,Schema Registry)
	@$(COMPOSE) run --rm --entrypoint python3 schema-init /schemas/check-schemas.py

.PHONY: ksql-ddl
ksql-ddl:
	$(call say,ksqlDB streams and tables)
	@$(COMPOSE) run --rm ksql-init

# The check reads the declared names out of ksql/*.sql. ksql-init is the
# container that has that directory mounted. The server holds the answer.
.PHONY: verify-ksqldb
verify-ksqldb:
	$(call say,ksqlDB)
	@$(COMPOSE) run --rm --entrypoint python3 ksql-init /ksql/check-ksql.py
