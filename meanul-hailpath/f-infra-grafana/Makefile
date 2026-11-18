# f-infra-grafana — this piece's own operational targets.
#
# Included by the root Makefile (`include f-infra-grafana/Makefile`), so
# every target here runs from the repository root, against the shared
# master .env — never `cd f-infra-grafana && make`. What this component is
# and how to verify it: README.md.

# =============================================================================
# Grafana
# =============================================================================

.PHONY: grafana-health
grafana-health:
	$(call say,Grafana)
	@$(COMPOSE) exec -T hailpath-grafana wget -qO- http://localhost:3000/api/health 2>/dev/null || true
	@printf "\n  Provisioned dashboards live under Dashboards → Hailpath.\n"
	@printf "  Geomap tiles are hailpath-tiles — checked by $(C)make tiles-health$(X).\n"

# Hit hailpath-tiles on hailpath-net, not a host loopback. Docker published
# ports plus route_localnet=0 used to make 127.0.0.1 look like a tiles
# failure on a healthy stack.
.PHONY: tiles-health
tiles-health:
	$(call say,Tiles)
	@root=$$(grep -E '^HAILPATH_VOLUME_ROOT=' $(ENV_FILE) | tail -1 | cut -d= -f2-); \
	mb="$$root/hailpath-tiles-data/nyc.mbtiles"; \
	if [ ! -s "$$mb" ]; then \
		printf "  $(R)FAIL$(X)    %s missing — run $(C)make tiles-prepare$(X) once\n" "$$mb"; \
		exit 1; \
	fi; \
	printf "  $(G)ok$(X)      nyc.mbtiles %s\n" "$$(du -h "$$mb" | cut -f1)"; \
	hs=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' hailpath-tiles 2>/dev/null || true); \
	if [ "$$hs" != healthy ]; then \
		printf "  $(R)FAIL$(X)    hailpath-tiles is '%s' — start it: $(C)docker compose up -d hailpath-tiles$(X)\n" "$${hs:-not running}"; \
		exit 1; \
	fi; \
	printf "  $(G)ok$(X)      hailpath-tiles healthy\n"; \
	base="http://hailpath-tiles:8080"; \
	json=$$($(COMPOSE) exec -T hailpath-grafana wget -S -O /dev/null "$$base/styles.json" 2>&1 || true); \
	if ! printf '%s' "$$json" | grep -q ' 200 OK'; then \
		printf "  $(R)FAIL$(X)    GET hailpath-tiles /styles.json did not return 200.\n"; \
		printf '%s\n' "$$json" | sed -n '1,6p' | sed 's/^/              /'; \
		exit 1; \
	fi; \
	printf "  $(G)ok$(X)      hailpath-tiles /styles.json 200\n"; \
	png=$$($(COMPOSE) exec -T hailpath-grafana wget -S -O /dev/null "$$base/styles/hailpath/11/602/768.png" 2>&1 || true); \
	if printf '%s' "$$png" | grep -q ' 200 OK' && printf '%s' "$$png" | grep -qi 'Content-Type: *image/png'; then \
		printf "  $(G)ok$(X)      hailpath-tiles /styles/hailpath/11/602/768.png 200 image/png\n"; \
	else \
		printf "  $(R)FAIL$(X)    GET hailpath-tiles /styles/hailpath/11/602/768.png did not return 200 image/png\n"; \
		printf '%s\n' "$$png" | sed -n '1,6p' | sed 's/^/              /'; \
		exit 1; \
	fi; \
	printf "  Grafana geomap uses hailpath-tiles, no MapTiler key.\n"

.PHONY: grafana-check
grafana-check:
	@python3 f-infra-grafana/check-dashboards.py

# A declared panel is not a working one, which is the reason: grafana-check
# proves a panel's SQL is well-formed and reads the right tables, but not
# that it RUNS. A renamed column or a function this ClickHouse version does
# not have is a panel that renders an error to whoever opens it first.
#
# stderr is folded into the captured output on purpose. clickhouse-client
# stops at the first bad statement and exits non-zero; without 2>&1 and the
# `|| true` the recipe would die before printing anything, which is exactly
# how `make capacity` once failed with Error 1 and no output at all.
.PHONY: verify-grafana
verify-grafana:
	$(call say,Grafana panels)
	@out=$$(python3 f-infra-grafana/panel-probe.py \
		| $(COMPOSE) exec -T hailpath-clickhouse clickhouse-client \
			--user "$(call getenv,CH_USER)" \
			--password "$(call getenv,CH_PASSWORD)" \
			--database hailpath --multiquery 2>&1 || true); \
	printf '%s\n' "$$out"; \
	if printf '%s' "$$out" | grep -qE 'FAIL|DB::Exception'; then \
		printf "\n  $(R)A panel did not run, or returned nothing.$(X)\n"; \
		printf "  clickhouse-client stops at the first bad statement, so the\n"; \
		printf "  last panel named above is the one to look at.\n"; \
		exit 1; \
	fi; \
	printf "\n  $(G)Every panel ran and returned rows.$(X)\n"; \
	printf "  This does not prove a panel RENDERS - a wrong field name in an\n"; \
	printf "  override still needs eyes on the dashboard.\n"

# NYC OSM extract → OpenMapTiles MBTiles for tileserver-gl. Idempotent.
# The script lives in tiles/prepare.sh so a missing optional .env key
# cannot become a silent `pipefail` Error 1 (this Makefile uses bash -eu
# -o pipefail).
.PHONY: tiles-prepare
tiles-prepare:
	$(call say,NYC OSM tiles)
	@bash f-infra-grafana/tiles/prepare.sh "$(ENV_FILE)"

# Not part of make prepare, and not a default. See the script for why it
# exists and what it costs.
.PHONY: tiles-water-from-image
tiles-water-from-image:
	$(call say,Water polygons from the OpenMapTiles image)
	@bash f-infra-grafana/tiles/water-from-image.sh "$(ENV_FILE)"

.PHONY: grafana-plugin-prepare
grafana-plugin-prepare:
	$(call say,Grafana ClickHouse plugin)
	@bash f-infra-grafana/plugin-prepare.sh "$(ENV_FILE)"
