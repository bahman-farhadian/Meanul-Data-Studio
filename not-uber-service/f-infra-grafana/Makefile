# f-infra-grafana — this piece's own operational targets.
#
# Included by the root Makefile (`include f-infra-grafana/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd f-infra-grafana && make`. What this component is
# and how to verify it: README.md.

# =============================================================================
##@ f-infra-grafana
# =============================================================================

.PHONY: grafana-health
grafana-health: ## Grafana answers on its health endpoint
	$(call say,Grafana)
	@$(COMPOSE) exec -T grafana wget -qO- http://localhost:3000/api/health 2>/dev/null || true
	@printf "\n  Provisioned dashboards live under Connections → Dashboards → NUS.\n"

.PHONY: grafana-check
grafana-check: ## Dashboards query only ClickHouse Distributed tables
	@python3 f-infra-grafana/check-dashboards.py

# NYC OSM extract → OpenMapTiles MBTiles for tileserver-gl. Idempotent:
# skips when nyc.mbtiles is already on disk. Needs the internet once.
#
# Planetiler's own image lives on ghcr.io and --area=new-york is easy to
# get wrong. Download the Geofabrik PBF and the Planetiler jar on the
# host (same pattern as lion-fetch), then run a Docker Hub JRE. Failures
# print the docker/curl output instead of a bare Error 1.
.PHONY: tiles-prepare
tiles-prepare: ## Build nyc.mbtiles (Planetiler) for the Grafana map
	$(call say,NYC OSM tiles)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	[ -n "$$root" ] || { printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT is not set\n"; exit 1; }; \
	dir="$$root/nus-tiles-data"; mkdir -p "$$dir"; \
	if [ -s "$$dir/nyc.mbtiles" ]; then \
		printf "  skipped  $$dir/nyc.mbtiles already exists\n"; \
		exit 0; \
	fi; \
	pbf_url=$$(grep -E '^GEOFABRIK_NY_PBF_URL=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	[ -n "$$pbf_url" ] || pbf_url=https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf; \
	jar_url=$$(grep -E '^PLANETILER_JAR_URL=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	[ -n "$$jar_url" ] || jar_url=https://github.com/onthegomap/planetiler/releases/download/v0.8.4/planetiler.jar; \
	jre=$$(grep -E '^JAVA_JRE_IMAGE=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	[ -n "$$jre" ] || jre=eclipse-temurin:21-jre-jammy; \
	if [ ! -s "$$dir/new-york-latest.osm.pbf" ]; then \
		printf "  fetching $$pbf_url\n"; \
		curl -fL --retry 5 -o "$$dir/new-york-latest.osm.pbf.part" "$$pbf_url"; \
		mv "$$dir/new-york-latest.osm.pbf.part" "$$dir/new-york-latest.osm.pbf"; \
	else printf "  kept     $$dir/new-york-latest.osm.pbf\n"; fi; \
	if [ ! -s "$$dir/planetiler.jar" ]; then \
		printf "  fetching $$jar_url\n"; \
		curl -fL --retry 5 --retry-all-errors -o "$$dir/planetiler.jar.part" "$$jar_url"; \
		mv "$$dir/planetiler.jar.part" "$$dir/planetiler.jar"; \
	else printf "  kept     $$dir/planetiler.jar\n"; fi; \
	printf "  running  Planetiler in %s (writes /data/nyc.mbtiles)\n" "$$jre"; \
	docker run --rm --user 0:0 \
		-e JAVA_TOOL_OPTIONS="-Xmx16g" \
		-v "$$dir:/data" \
		"$$jre" \
		java -jar /data/planetiler.jar \
			--osm-path=/data/new-york-latest.osm.pbf \
			--download \
			--output=/data/nyc.mbtiles; \
	[ -s "$$dir/nyc.mbtiles" ] \
		|| { printf "  $(R)FAIL$(X)    planetiler did not write nyc.mbtiles\n"; exit 1; }; \
	printf "  wrote    $$dir/nyc.mbtiles (%s)\n" "$$(du -sh "$$dir/nyc.mbtiles" | cut -f1)"
