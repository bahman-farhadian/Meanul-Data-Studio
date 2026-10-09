.PHONY: verify-quality
verify-quality:
	$(call say,Data-quality bars)
	@out=$$($(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" \
		--password "$(call getenv,CH_PASSWORD)" --database nus --multiquery \
		--output-format PrettyCompactMonoBlock < z-config/quality.sql); \
	printf '%s\n' "$$out"; \
	if printf '%s' "$$out" | grep -q FAIL; then \
		printf "\n  $(R)A bar failed.$(X) These are structural invariants, not thresholds -\n"; \
		printf "  a failure is a real defect in the pipeline, not a tuning question.\n"; \
		exit 1; \
	fi; \
	printf "\n  $(G)Every bar passed.$(X)\n"

# make capacity's recipe is ONE continued shell command and must stay that
# way. Every comment about it lives here, above the target, because a `#`
# line at column 0 inside a backslash continuation terminates it: make then
# runs the fragments as separate shells, the second one loses every variable
# the first set, and under `bash -eu` it dies with "full: unbound variable"
# while echoing the recipe it was told not to echo. That is how this target
# broke, and moving the prose out is the fix rather than a tidy-up.
#
# The scale factor is derived, not typed: full-scale SEED_DRIVERS from
# .env.example against whatever this run actually used, so a dev profile
# that changes cannot leave a stale projection looking current.
#
# The quota is read out of mk/volume-quotas.mk, the only place a quota is
# applied. It used to read CH_VOLUME_QUOTA_GB,
# a second variable that was never set in .env - so the projection was
# judged against a 96 GB default while 112 was actually in force.
#
# Every grep ends in `|| true`: under bash -eu -o pipefail a grep that
# matches nothing exits 1 and kills the target before a single line prints,
# which is the least diagnosable shape a failure can take.
.PHONY: capacity
capacity:
	$(call say,Warehouse capacity)
	@full=$$(grep -E '^SEED_DRIVERS=' .env.example | tail -1 | cut -d= -f2- || true); \
	now=$$(grep -E '^SEED_DRIVERS=' $(ENV_FILE) | tail -1 | cut -d= -f2- || true); \
	quota=$$(grep -oE 'nus-ch-data-s1r1:[0-9]+g' mk/volume-quotas.mk | head -1 | sed 's/.*://; s/g$$//' || true); \
	quota=$${quota:-96}; \
	if [ -z "$$full" ] || [ -z "$$now" ]; then \
		printf "  $(R)FAIL$(X)    SEED_DRIVERS missing from .env or .env.example\n"; exit 1; \
	fi; \
	scale=$$(awk -v a="$$full" -v b="$$now" 'BEGIN{printf "%.4f", (b>0? a/b : 1)}'); \
	printf "  full-scale SEED_DRIVERS %s / this run %s = scale x%s, quota %sGB/node\n\n" \
		"$$full" "$$now" "$$scale" "$$quota"; \
	$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" \
		--password "$(call getenv,CH_PASSWORD)" --database nus --multiquery \
		--param_scale="$$scale" --param_quota_gb="$$quota" \
		--output-format PrettyCompactMonoBlock < z-config/capacity.sql

.PHONY: profile
profile:
	$(call say,What the warehouse actually holds)
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" \
		--password "$(call getenv,CH_PASSWORD)" --database nus --multiquery \
		--output-format PrettyCompactMonoBlock < z-config/profile.sql
	$(call say,Kafka — how many messages each topic actually holds)
	@printf "  %-28s %10s %10s\n" TOPIC PARTITIONS MESSAGES
	@$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-get-offsets.sh \
		--bootstrap-server nus-kafka-1:9092 2>/dev/null \
		| awk -F: '$$1 !~ /^(__|_schemas|connect_)/ {parts[$$1]++; total[$$1]+=$$3} \
		           END{for (t in total) printf "  %-28s %10d %10d\n", t, parts[t], total[t]}' \
		| sort || true
	@printf "\n  A topic at 0 messages has never been written to. That is where a stalled\n"
	@printf "  pipeline shows itself — every consumer downstream of it will read 0 lag\n"
	@printf "  while doing nothing at all.\n\n"
	@for g in $(GROUPS); do \
		$(COMPOSE) exec -T kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
			--bootstrap-server nus-kafka-1:9092 --describe --group "$$g" 2>/dev/null \
			| awk -v g="$$g" 'NR>1 && $$6 != "-" {lag+=$$6; n++} END{if(n) printf "  %-24s partitions=%d total_lag=%d\n", g, n, lag}' || true; \
	done
	$(call say,Redis — what the services put in the cache)
	@printf "  %-12s %s\n" "db" "keys"
	@i=0; for name in system driver passenger trip demand; do \
		n=$$($(COMPOSE) exec -T redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning \
			-n $$i DBSIZE 2>/dev/null | tr -d '\r'); \
		printf "  %-12s %s\n" "$$i $$name" "$$n"; \
		i=$$((i+1)); \
	done
	@$(COMPOSE) exec -T redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning \
		--scan --count 1000 2>/dev/null \
		| sed -E 's/[0-9a-f-]{8,}.*//; s/[0-9]+$$//' | sort | uniq -c | sort -rn | head -20 \
		| awk '{printf "  %8s  %s*\n", $$1, $$2}' || true
	@printf "  bootstrap flag: %s\n" "$$($(COMPOSE) exec -T redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning get system:bootstrap:done 2>/dev/null | tr -d '\r')"
	@printf "  SCAN above is db 0 only. driver:* lives in db 1; 0 keys there means\n"
	@printf "  cache-updater has not applied cdc.drivers and the fleet will not start.\n"

