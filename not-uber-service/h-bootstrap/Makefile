# h-bootstrap — this piece's own operational targets.
#
# Included by the root Makefile (`include h-bootstrap/Makefile`), so every
# target here runs from the repository root, against the shared master
# .env — never `cd h-bootstrap && make`. What this component is and how to
# verify it: README.md.
#
# This is where the generator/seeding work belongs: tuning history.py's
# randomness (trip length, the outcome mix, the price-multiplier curve,
# how a demand score turns into "no driver available"), previewing what a
# given seed and settings produce before loading it, that kind of target.
# Nothing like that exists yet — add it here when it does.

# =============================================================================
##@ h-bootstrap
# =============================================================================

.PHONY: bootstrap
bootstrap: ## Run h-bootstrap and follow it (the map import is the long quiet part)
	$(call say,Bootstrap — schema, street map, people, a week of history)
	@printf "  This takes a while: the OSM import is the slow step.\n"
	@if $(COMPOSE) run --rm bootstrap; then \
		printf "\n  $(G)ok$(X)      bootstrap finished; system:bootstrap:done is set\n"; \
	else \
		printf "\n  $(R)FAIL$(X)    bootstrap failed — the services stay in standby on purpose.\n"; \
		printf "              Read the log above, fix it, then: make bootstrap\n"; exit 1; \
	fi

.PHONY: osm-fetch
osm-fetch: ## Download the street map on the HOST, so bootstrap does not have to
	$(call say,Fetching the street map on the host)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	url=$$(grep -E '^OSM_URL=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	dest="$$root/nus-osm-data"; file="$$dest/region-latest.osm.pbf"; \
	mkdir -p "$$dest"; \
	if [ -s "$$file" ]; then \
		printf "  $(G)ok$(X)      already there: %s (%s)\n" "$$file" "$$(du -h "$$file" | cut -f1)"; \
	else \
		printf "  from    %s\n  to      %s\n" "$$url" "$$file"; \
		curl -fL --progress-bar -o "$$file.part" "$$url" || { rm -f "$$file.part"; \
			printf "  $(R)FAIL$(X)    download failed. This runs on the HOST, so if it cannot reach\n"; \
			printf "              Geofabrik either, neither will bootstrap.\n"; exit 1; }; \
		mv "$$file.part" "$$file"; \
		printf "  $(G)ok$(X)      %s\n" "$$(du -h "$$file" | cut -f1)"; \
	fi; \
	$(MAKE) --no-print-directory volume-perms >/dev/null 2>&1 || true; \
	printf "  $(G)ok$(X)      handed to the bootstrap user; it will skip the download\n"

.PHONY: verify-data
verify-data: ## The marker, the seeded rows, the street graph, and the warehouse
	$(call say,Data)
	@printf "  bootstrap marker: "
	@$(COMPOSE) exec -T redis-1 redis-cli -a "$(call getenv,REDIS_PASSWORD)" --no-auth-warning \
		get system:bootstrap:done 2>/dev/null || printf "(not set)\n"
	@$(COMPOSE) exec -T pg-1 psql -U postgres -t -c \
		"SELECT 'drivers      ' || count(*) FROM drivers UNION ALL \
		 SELECT 'passengers   ' || count(*) FROM passengers UNION ALL \
		 SELECT 'trips        ' || count(*) FROM trips UNION ALL \
		 SELECT 'city_zones   ' || count(*) FROM city_zones UNION ALL \
		 SELECT 'road segments' || count(*) FROM ways UNION ALL \
		 SELECT 'seg. traffic ' || count(*) FROM segment_traffic;" 2>/dev/null | sed 's/^ */  /' || true
	@$(COMPOSE) exec -T ch-s1r1 clickhouse-client --user "$(call getenv,CH_USER)" --password "$(call getenv,CH_PASSWORD)" \
		--query "SELECT 'trip_events ' || toString(count()) FROM nus.trip_events" 2>/dev/null | sed 's/^/  /' || true
	@printf "\n  Trips should be roughly HISTORY_DAYS x HISTORY_TRIPS_PER_DAY, and 'ways'\n"
	@printf "  in the hundreds of thousands. 'ways' at 0 means the map import did not run.\n"
