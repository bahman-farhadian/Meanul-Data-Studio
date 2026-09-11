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
bootstrap: ## Run h-bootstrap and follow it (schema, people, a week of history)
	$(call say,Bootstrap — schema, street graph restore, people, a week of history)
	@$(MAKE) --no-print-directory pg-cpus-bootstrap
	@if $(COMPOSE) run --rm bootstrap; then \
		printf "\n  $(G)ok$(X)      bootstrap finished; system:bootstrap:done is set\n"; \
		$(MAKE) --no-print-directory pg-cpus-normal; \
	else \
		printf "\n  $(R)FAIL$(X)    bootstrap failed — the services stay in standby on purpose.\n"; \
		printf "              PG_CPUS stays raised so a retry does not re-pay the ramp-up cost.\n"; \
		printf "              Read the log above, fix it, then: make bootstrap\n"; exit 1; \
	fi

.PHONY: lion-fetch
lion-fetch: ## Download NYC's official LION street data on the HOST
	$(call say,Fetching the LION street map on the host)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	url=$$(grep -E '^LION_URL=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	dest="$$root/nus-lion-data"; file="$$dest/lion.zip"; \
	mkdir -p "$$dest"; \
	if [ -s "$$file" ]; then \
		printf "  $(G)ok$(X)      already there: %s (%s)\n" "$$file" "$$(du -h "$$file" | cut -f1)"; \
	else \
		printf "  from    %s\n  to      %s\n" "$$url" "$$file"; \
		curl -fL --progress-bar -o "$$file.part" "$$url" || { rm -f "$$file.part"; \
			printf "  $(R)FAIL$(X)    download failed. This runs on the HOST, so if it cannot reach\n"; \
			printf "              NYC Open Data either, neither will lion-prepare.\n"; exit 1; }; \
		mv "$$file.part" "$$file"; \
		printf "  $(G)ok$(X)      %s\n" "$$(du -h "$$file" | cut -f1)"; \
	fi

.PHONY: tlc-zones-fetch
tlc-zones-fetch: ## Download NYC TLC's real taxi zones on the HOST
	$(call say,Fetching NYC TLC's real taxi zones on the host)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	url=$$(grep -E '^TLC_ZONES_URL=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	dest="$$root/nus-lion-data"; file="$$dest/taxi-zones.geojson"; \
	mkdir -p "$$dest"; \
	if [ -s "$$file" ]; then \
		printf "  $(G)ok$(X)      already there: %s (%s)\n" "$$file" "$$(du -h "$$file" | cut -f1)"; \
	else \
		printf "  from    %s\n  to      %s\n" "$$url" "$$file"; \
		curl -fL --progress-bar -o "$$file.part" "$$url" || { rm -f "$$file.part"; \
			printf "  $(R)FAIL$(X)    download failed. This runs on the HOST, so if it cannot reach\n"; \
			printf "              NYC Open Data either, neither will lion-prepare.\n"; exit 1; }; \
		mv "$$file.part" "$$file"; \
		printf "  $(G)ok$(X)      %s\n" "$$(du -h "$$file" | cut -f1)"; \
	fi

.PHONY: tlc-trips-fetch
tlc-trips-fetch: ## Download one real month of TLC trip records on the HOST
	$(call say,Fetching TLC's real trip records on the host)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	month=$$(grep -E '^TLC_TRIP_DATA_MONTH=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	month=$${month:-2025-01}; \
	url_template=$$(grep -E '^TLC_TRIP_DATA_URL_TEMPLATE=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	url_template=$${url_template:-https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_{month}.parquet}; \
	url=$$(printf '%s' "$$url_template" | sed "s/{month}/$$month/"); \
	dest="$$root/nus-lion-data"; file="$$dest/tlc-trips-$$month.parquet"; \
	mkdir -p "$$dest"; \
	if [ -s "$$file" ]; then \
		printf "  $(G)ok$(X)      already there: %s (%s)\n" "$$file" "$$(du -h "$$file" | cut -f1)"; \
	else \
		printf "  from    %s\n  to      %s\n" "$$url" "$$file"; \
		printf "  A few hundred MB, real billed traffic - fetched once here and reused\n"; \
		printf "  by every zone-demand-prepare run after this one, the same as lion.zip\n"; \
		printf "  and taxi-zones.geojson above.\n\n"; \
		curl -fL --progress-bar -o "$$file.part" "$$url" || { rm -f "$$file.part"; \
			printf "  $(R)FAIL$(X)    download failed. This runs on the HOST, so if it cannot reach\n"; \
			printf "              CloudFront either, neither will zone-demand-prepare.\n"; exit 1; }; \
		mv "$$file.part" "$$file"; \
		printf "  $(G)ok$(X)      %s\n" "$$(du -h "$$file" | cut -f1)"; \
	fi

.PHONY: lion-prepare
lion-prepare: ## Build the routable graph from LION, once (the slow step, cached after)
	$(call say,Preparing the routable street graph)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	dir="$$root/nus-lion-data"; dump="$$dir/routable-graph.dump"; \
	lion_pg_image=$$(grep -E '^LION_PG_IMAGE=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	lion_pg_image=$${lion_pg_image:-postgis/postgis:17-3.5}; \
	NUS_LION_DATA_DIR="$$dir" $(COMPOSE) -f h-bootstrap/lion-prepare/docker-compose.yaml --env-file $(ENV_FILE) down -v --remove-orphans >/dev/null 2>&1 || true; \
	dump_has_zones=false; \
	if [ -s "$$dump" ] && docker run --rm -v "$$dump:/data/dump:ro" "$$lion_pg_image" \
		pg_restore -l /data/dump 2>/dev/null | grep -q "TABLE public city_zones_source"; then \
		dump_has_zones=true; \
	fi; \
	if [ -s "$$dump" ] && [ "$$dump_has_zones" = false ]; then \
		printf "  $(Y)warn$(X)    cached dump exists but predates the TLC-zones step (no\n"; \
		printf "              city_zones_source) - rebuilding rather than trusting a stale cache\n"; \
	fi; \
	if [ "$$dump_has_zones" = true ]; then \
		printf "  $(G)ok$(X)      already built: %s (%s)\n" "$$dump" "$$(du -h "$$dump" | cut -f1)"; \
	else \
		printf "  This is the slow step - filtering, cost computation and topology-\n"; \
		printf "  building over the whole LION dataset. It runs once; every bring-up\n"; \
		printf "  after this one restores the cached result instead.\n\n"; \
		NUS_LION_DATA_DIR="$$dir" $(COMPOSE) -f h-bootstrap/lion-prepare/docker-compose.yaml --env-file $(ENV_FILE) up --build --exit-code-from lion-prepare; \
		status=$$?; \
		NUS_LION_DATA_DIR="$$dir" $(COMPOSE) -f h-bootstrap/lion-prepare/docker-compose.yaml --env-file $(ENV_FILE) down -v; \
		if [ "$$status" -ne 0 ] || [ ! -s "$$dump" ]; then \
			printf "  $(R)FAIL$(X)    lion-prepare did not produce %s\n" "$$dump"; exit 1; \
		fi; \
		printf "  $(G)ok$(X)      %s (%s)\n" "$$dump" "$$(du -h "$$dump" | cut -f1)"; \
	fi; \
	$(MAKE) --no-print-directory volume-perms >/dev/null 2>&1 || true; \
	printf "  $(G)ok$(X)      handed to the bootstrap user; it will skip straight to restoring it\n"

.PHONY: zone-demand-prepare
zone-demand-prepare: ## Build real TLC-trip-record demand calibration, once (cached after)
	$(call say,Preparing real demand calibration from TLC's own trip records)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	dir="$$root/nus-lion-data"; \
	zone_out="$$dir/zone_demand_calibration.csv"; od_out="$$dir/od_pair_calibration.csv"; \
	NUS_LION_DATA_DIR="$$dir" $(COMPOSE) -f h-bootstrap/zone-demand-prepare/docker-compose.yaml --env-file $(ENV_FILE) down --remove-orphans >/dev/null 2>&1 || true; \
	if [ -s "$$zone_out" ] && [ -s "$$od_out" ]; then \
		printf "  $(G)ok$(X)      already built: %s, %s\n" "$$zone_out" "$$od_out"; \
	else \
		printf "  Reads the trip records tlc-trips-fetch already downloaded to the\n"; \
		printf "  host, column-projected, and reduces them to two small calibration\n"; \
		printf "  tables. Takes several minutes; runs once.\n\n"; \
		NUS_LION_DATA_DIR="$$dir" $(COMPOSE) -f h-bootstrap/zone-demand-prepare/docker-compose.yaml --env-file $(ENV_FILE) up --build --exit-code-from zone-demand-prepare; \
		status=$$?; \
		NUS_LION_DATA_DIR="$$dir" $(COMPOSE) -f h-bootstrap/zone-demand-prepare/docker-compose.yaml --env-file $(ENV_FILE) down; \
		if [ "$$status" -ne 0 ] || [ ! -s "$$zone_out" ] || [ ! -s "$$od_out" ]; then \
			printf "  $(R)FAIL$(X)    zone-demand-prepare did not produce %s and %s\n" "$$zone_out" "$$od_out"; exit 1; \
		fi; \
		printf "  $(G)ok$(X)      %s, %s\n" "$$zone_out" "$$od_out"; \
	fi; \
	$(MAKE) --no-print-directory volume-perms >/dev/null 2>&1 || true

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
