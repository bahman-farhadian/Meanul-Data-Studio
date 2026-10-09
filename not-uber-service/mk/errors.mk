# A replica's WAL receiver can race the leader's own replication-slot
# creation on first bootstrap - a known Patroni timing window, not a stuck
# cluster, and it self-heals on Patroni's own retry. verify-pg (one Leader,
# two Replicas, lag 0) is the real signal for whether it actually got stuck.
.PHONY: errors
errors:
	$(call say,Errors across the stack)
	@found=0; \
	for c in $$($(COMPOSE) ps -aq); do \
		name=$$(docker inspect -f '{{slice .Name 1}}' "$$c" 2>/dev/null); \
		state=$$(docker inspect -f '{{.State.Status}}' "$$c" 2>/dev/null); \
		code=$$(docker inspect -f '{{.State.ExitCode}}' "$$c" 2>/dev/null); \
		hs=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$$c" 2>/dev/null); \
		oomed=$$(docker inspect -f '{{.State.OOMKilled}}' "$$c" 2>/dev/null); \
		restarts=$$(docker inspect -f '{{.RestartCount}}' "$$c" 2>/dev/null); \
		bad=""; \
		[ "$$state" = exited ] && [ "$$code" != 0 ] && bad="exited $$code"; \
		[ "$$hs" = unhealthy ] && bad="$$bad unhealthy"; \
		[ "$$state" = restarting ] && bad="$$bad restarting"; \
		[ "$$oomed" = true ] && bad="$$bad oom-killed"; \
		[ "$${restarts:-0}" -gt 3 ] && bad="$$bad restart-looping(x$$restarts)"; \
		logs=$$(docker logs --since $(ERRORS_WINDOW) "$$c" 2>&1 \
			| grep -E '"level": ?"(ERROR|CRITICAL)"|level=error|<Error>|ERROR:|FATAL:|\[error\]|Traceback \(most recent|^[A-Za-z_.]*(Error|Exception):' \
			| grep -Ev 'FATAL: +the database system is starting up' \
			| grep -Ev 'replication slot "[^"]+" does not exist' \
			| tail -6 || true); \
		[ -z "$$bad" ] && [ -z "$$logs" ] && continue; \
		found=1; \
		printf "\n  $(B)%s$(X)  $(R)%s$(X)\n" "$$name" "$${bad:-log errors only}"; \
		if [ "$$hs" = unhealthy ]; then \
			out=$$(docker inspect -f '{{if .State.Health}}{{range .State.Health.Log}}{{.Output}}{{end}}{{end}}' "$$c" 2>/dev/null | tail -3); \
			[ -n "$$out" ] && printf "    $(Y)healthcheck said:$(X)\n%s\n" "$$(echo "$$out" | sed 's/^/      /')"; \
		fi; \
		[ -n "$$logs" ] && printf "%s\n" "$$(echo "$$logs" | cut -c1-150 | sed 's/^/      /')"; \
	done; \
	if [ "$$found" = 0 ]; then \
		printf "  $(G)ok$(X)      nothing exited badly, nothing unhealthy, nothing OOM-killed, no errors in the logs\n"; \
	else \
		printf "\n  An OOM kill or a restart loop means that component's limit or the\n"; \
		printf "  pacing must come down. Full log for any of them: $(C)make logs SVC=<name>$(X)\n"; \
		printf "  Confirmed everything above is a known, harmless startup race (verify-*\n"; \
		printf "  clean, ps healthy)? $(C)make errors-ack$(X) clears it, so the next run only\n"; \
		printf "  reports what actually happens after that point.\n"; \
	fi
	@printf "\n"

.PHONY: errors-ack
errors-ack:
	$(call say,Clearing log history)
	@for c in $$($(COMPOSE) ps -aq); do \
		name=$$(docker inspect -f '{{slice .Name 1}}' "$$c" 2>/dev/null); \
		path=$$(docker inspect -f '{{.LogPath}}' "$$c" 2>/dev/null); \
		if [ -z "$$path" ]; then \
			printf "  $(Y)skip$(X)    %s (no LogPath - not the json-file log driver?)\n" "$$name"; \
		elif [ -w "$$path" ] 2>/dev/null; then \
			: > "$$path" && printf "  $(G)cleared$(X)  %s\n" "$$name"; \
		else \
			printf "  $(Y)skip$(X)    %s (no permission - run as root)\n" "$$name"; \
		fi; \
	done
	@printf "\n  $(C)make errors$(X) now only reports what happens after this point - it is\n"
	@printf "  not a substitute for fixing anything, only for not re-reading the same\n"
	@printf "  known-good startup noise on every future check.\n\n"

