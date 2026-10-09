.PHONY: wait-live
wait-live:
	$(call say,Waiting for the live marketplace)
	@printf "  driver-service start-up is the critical path, and it grows with the\n"
	@printf "  fleet: it waits for cache-updater, builds the road point pools, reads\n"
	@printf "  the roster, then computes one pgRouting path per online driver.\n\n"
	@deadline=$$(( $$(date +%s) + $(WAIT_LIVE_TIMEOUT) )); \
	while ! docker logs driver-service 2>&1 | grep -q '"message": "tick"'; do \
		if [ $$(date +%s) -ge $$deadline ]; then \
			printf "  $(R)FAIL$(X)    driver-service never reached its first tick in $(WAIT_LIVE_TIMEOUT)s\n"; \
			printf "  It is not necessarily broken - look at where it stopped:\n"; \
			printf "    $(C)docker logs driver-service 2>&1 | grep -v 'no street path' | tail -20$(X)\n"; \
			printf "  The last INFO line names the phase. Raise WAIT_LIVE_TIMEOUT if it\n"; \
			printf "  is simply still working.\n"; \
			exit 1; \
		fi; \
		sleep 15; \
	done; \
	printf "  $(G)ok$(X)      the fleet is on the road\n"
	@printf "\n  Now letting live trips run and END for $(WAIT_LIVE_SETTLE)s. A trip takes about\n"
	@printf "  nine minutes, and verify-positions only reads trips that already ended,\n"
	@printf "  so an online fleet is not yet something the checks can measure.\n"
	@sleep $(WAIT_LIVE_SETTLE)
	$(call ok,live traffic has had time to complete trips)

