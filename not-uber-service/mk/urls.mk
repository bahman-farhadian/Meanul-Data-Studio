.PHONY: urls
urls:
	@def=$$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'dev [a-z0-9.-]+' | awk '{print $$2}' | head -1 || true); \
	ips=$$(ip -4 -o addr show scope global 2>/dev/null \
		| awk '{gsub(/\/.*/,"",$$4); print $$2, $$4}' \
		| grep -vE '^(docker[0-9]*|br-[0-9a-f]+|veth[0-9a-f]*) ' || true); \
	printf "\n  The entry tier binds $(B)0.0.0.0$(X), so it answers on every address\n"; \
	printf "  this host has — pick whichever one you can actually reach from where\n"; \
	printf "  you are connecting:\n\n"; \
	if [ -z "$$ips" ]; then printf "    (none found — is this host on a network at all?)\n"; \
	else echo "$$ips" | while read -r ifc addr; do \
		mark=""; [ "$$ifc" = "$$def" ] && mark=" (default route)"; \
		printf "    %-16s %-16s%s\n" "$$addr" "$$ifc" "$$mark"; \
	done; fi; \
	printf "\n  Substitute one of those for $(C)<host>$(X) below — every port is the same\n"; \
	printf "  regardless of which address you reach it on.\n\n"; \
	printf "  %-18s %s\n" "PostgreSQL writes" "<host>:$(call getenv,LB_A_PG_WRITE_PORT,5432)"; \
	printf "  %-18s %s\n" "PostgreSQL reads"  "<host>:$(call getenv,LB_A_PG_READ_PORT,5433)"; \
	printf "  %-18s %s\n" "Redis writes"      "<host>:$(call getenv,LB_A_REDIS_WRITE_PORT,6379)"; \
	printf "  %-18s %s\n" "Redis reads"       "<host>:$(call getenv,LB_A_REDIS_READ_PORT,6380)"; \
	printf "  %-18s %s\n" "ksqlDB"            "http://<host>:$(call getenv,LB_A_KSQLDB_PORT,8089)"; \
	printf "  %-18s %s\n" "Debezium Connect"  "http://<host>:$(call getenv,LB_A_DEBEZIUM_PORT,8083)"; \
	printf "  %-18s %s\n" "ClickHouse HTTP"   "<host>:$(call getenv,LB_A_CH_HTTP_PORT,8123)"; \
	printf "  %-18s %s\n" "ClickHouse native" "<host>:$(call getenv,LB_A_CH_NATIVE_PORT,9000)"; \
	printf "  %-18s %s\n" "Grafana"           "http://<host>:$(call getenv,LB_A_GRAFANA_PORT,3000)"; \
	printf "  %-18s %s\n" "Superset"          "http://<host>:$(call getenv,LB_A_SUPERSET_PORT,8088)"; \
	printf "  %-18s %s\n" "HAProxy stats"     "http://<host>:$(call getenv,LB_A_STATS_PORT,8404)/stats"; \
	printf "\n  A SQL client such as DBeaver connects straight to the PostgreSQL and\n"; \
	printf "  ClickHouse addresses above. Kafka is the one broker below. A client\n"; \
	printf "  is handed the address set by $(C)KAFKA_ADVERTISED_HOST_A$(X)/$(C)_B$(X)\n"; \
	printf "  in $(ENV_FILE), not $(B)<host>$(X).\n\n"; \
	printf "  %-10s %-24s %s\n" "" "via address A" "via address B"; \
	printf "  %-10s %-24s %s\n" "nus-kafka" "$(call getenv,KAFKA_ADVERTISED_HOST_A):9094" "$(call getenv,KAFKA_ADVERTISED_HOST_B):9097"; \
	printf "\n"

