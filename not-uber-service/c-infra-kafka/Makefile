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

.PHONY: verify-ksqldb
verify-ksqldb: ## ksqlDB's own status, and every stream/table it currently knows about
	$(call say,ksqlDB)
	@$(COMPOSE) exec -T ksqldb-server python3 -c "import urllib.request,json,base64,os; req=urllib.request.Request('http://localhost:8088/info'); req.add_header('Authorization','Basic '+base64.b64encode(('%s:%s'%(os.environ['KSQLDB_ADMIN_USER'],os.environ['KSQLDB_ADMIN_PASSWORD'])).encode()).decode()); print(json.dumps(json.loads(urllib.request.urlopen(req, timeout=5).read()), indent=2))"
	@echo
	@$(COMPOSE) exec -T ksqldb-server python3 -c "import urllib.request,json,base64,os; req=urllib.request.Request('http://localhost:8088/ksql', data=json.dumps({'ksql':'SHOW STREAMS;'}).encode(), headers={'Content-Type':'application/vnd.ksql.v1+json'}); req.add_header('Authorization','Basic '+base64.b64encode(('%s:%s'%(os.environ['KSQLDB_ADMIN_USER'],os.environ['KSQLDB_ADMIN_PASSWORD'])).encode()).decode()); print(urllib.request.urlopen(req, timeout=5).read().decode())"
