.PHONY: preflight
preflight:
	$(call say,Preflight)
	@fail=0; \
	command -v docker >/dev/null 2>&1 \
		&& printf "  $(G)ok$(X)      docker %s\n" "$$(docker version -f '{{.Server.Version}}' 2>/dev/null || echo '(daemon unreachable)')" \
		|| { printf "  $(R)FAIL$(X)    docker is not installed\n"; fail=1; }; \
	docker info >/dev/null 2>&1 \
		|| { printf "  $(R)FAIL$(X)    the Docker daemon is not reachable (are you root / in the docker group?)\n"; fail=1; }; \
	cv=$$($(COMPOSE) version --short 2>/dev/null || echo none); \
	case "$$cv" in none) printf "  $(R)FAIL$(X)    the compose plugin is missing\n"; fail=1;; \
		1.*) printf "  $(R)FAIL$(X)    compose $$cv is v1; this stack needs v2+ for include:\n"; fail=1;; \
		*)   printf "  $(G)ok$(X)      compose %s\n" "$$cv";; esac; \
	cores=$$(nproc); mem=$$(awk '/MemTotal/{printf "%d", $$2/1024/1024}' /proc/meminfo); \
	[ "$$cores" -ge 20 ] && printf "  $(G)ok$(X)      %s cores (budget assumes 20)\n" "$$cores" \
		|| printf "  $(Y)warn$(X)    %s cores; the budget assumes 20 — turn the pacing down\n" "$$cores"; \
	[ "$$mem" -ge 118 ] && printf "  $(G)ok$(X)      %s GB RAM (budget needs ~112 GB at the bootstrap peak)\n" "$$mem" \
		|| printf "  $(Y)warn$(X)    %s GB RAM; the budget peaks near 112 GB during bootstrap\n" "$$mem"; \
	root=$$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker); \
	avail=$$(df -BG --output=avail "$$root" 2>/dev/null | tail -1 | tr -dc '0-9'); \
	if [ -n "$$avail" ] && [ "$$avail" -ge 100 ]; then \
		printf "  $(G)ok$(X)      %s GB free on %s\n" "$$avail" "$$root"; \
	else \
		printf "  $(Y)warn$(X)    only %s GB free on %s — images are ~15 GB and ClickHouse grows ~1-2 GB/day.\n" "$$avail" "$$root"; \
		printf "              Move Docker's data-root to a big disk before deploying.\n"; \
	fi; \
	if [ ! -f $(ENV_FILE) ]; then \
		printf "  $(R)FAIL$(X)    no $(ENV_FILE) — run 'make init' first\n"; fail=1; \
	else \
		printf "  $(G)ok$(X)      $(ENV_FILE) present\n"; \
		left=$$(grep -cE '^[A-Z_]+=.*change-me' $(ENV_FILE) || true); \
		if [ "$$left" -gt 0 ]; then \
			printf "  $(R)FAIL$(X)    %s setting(s) still hold a change-me placeholder:\n" "$$left"; \
			grep -nE '^[A-Z_]+=.*change-me' $(ENV_FILE) | sed 's/=.*/=.../' | sed 's/^/              /'; \
			fail=1; \
		else printf "  $(G)ok$(X)      no change-me placeholders left\n"; fi; \
		missing=""; \
		for v in PG_SUPERUSER_PASSWORD PG_REPLICATION_PASSWORD PATRONI_REST_PASSWORD REDIS_PASSWORD \
		         CH_PASSWORD GRAFANA_ADMIN_PASSWORD SUPERSET_ADMIN_PASSWORD SUPERSET_SECRET_KEY \
		         KAFKA_CLUSTER_ID KSQLDB_ADMIN_PASSWORD; do \
			grep -qE "^$$v=.+" $(ENV_FILE) || missing="$$missing $$v"; done; \
		[ -z "$$missing" ] && printf "  $(G)ok$(X)      every required setting has a value\n" \
			|| { printf "  $(R)FAIL$(X)    missing or empty:%s\n" "$$missing"; fail=1; }; \
		dupes=$$(grep -oE '^[A-Z_][A-Z0-9_]*=' $(ENV_FILE) | sort | uniq -d | tr -d '=' | tr '\n' ' ' || true); \
		[ -z "$$dupes" ] && printf "  $(G)ok$(X)      no setting is defined twice\n" \
			|| printf "  $(Y)warn$(X)    defined more than once (the last wins): %s\n" "$$dupes"; \
	fi; \
	root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -z "$$root" ]; then printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT is not set in $(ENV_FILE)\n"; fail=1; \
	elif [ ! -d "$$root" ]; then printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT %s does not exist — run 'make init'\n" "$$root"; fail=1; \
	elif [ ! -w "$$root" ]; then printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT %s is not writable\n" "$$root"; fail=1; \
	else \
		vfree=$$(df -BG --output=avail "$$root" 2>/dev/null | tail -1 | tr -dc '0-9'); \
		vdev=$$(df --output=source "$$root" 2>/dev/null | tail -1); \
		if [ -n "$$vfree" ] && [ "$$vfree" -ge 200 ]; then \
			printf "  $(G)ok$(X)      data volumes -> %s (%s GB free on %s)\n" "$$root" "$$vfree" "$$vdev"; \
		else \
			printf "  $(Y)warn$(X)    data volumes -> %s, only %s GB free — ClickHouse grows 1-2 GB/day\n" "$$root" "$$vfree"; \
		fi; \
	fi; \
	docker network inspect $(NETWORK) >/dev/null 2>&1 \
		&& printf "  $(G)ok$(X)      network $(NETWORK) exists\n" \
		|| { printf "  $(R)FAIL$(X)    network $(NETWORK) is missing — run 'make init'\n"; fail=1; }; \
	missing=""; n=0; \
	for d in $$($(COMPOSE) config 2>/dev/null | grep -oE 'device: .+' | cut -d' ' -f2- || true); do \
		n=$$((n+1)); [ -d "$$d" ] || missing="$$missing $$(basename "$$d")"; done; \
	if [ "$$n" -eq 0 ]; then :; \
	elif [ -z "$$missing" ]; then \
		printf "  $(G)ok$(X)      all %s volume directories exist\n" "$$n"; \
	else \
		printf "  $(R)FAIL$(X)    volume directories the stack mounts do not exist:\n"; \
		for m in $$missing; do printf "                %s\n" "$$m"; done; \
		printf "              Run $(C)make init$(X) — it creates exactly the paths the compose\n"; \
		printf "              model mounts. A container cannot start without its own.\n"; \
		fail=1; \
	fi; \
	missingimg=""; nimg=0; \
	for i in $$($(COMPOSE) --profile init config --images 2>/dev/null | sort -u || true); do \
		nimg=$$((nimg+1)); docker image inspect "$$i" >/dev/null 2>&1 || missingimg="$$missingimg $$i"; done; \
	graph=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-)/nus-lion-data/routable-graph.dump; \
	mbtiles=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-)/nus-tiles-data/nyc.mbtiles; \
	if [ "$$nimg" -gt 0 ] && [ -z "$$missingimg" ] && [ -s "$$graph" ] && [ -s "$$mbtiles" ]; then \
		printf "  $(G)ok$(X)      all %s images built; LION graph and nyc.mbtiles are on disk\n" "$$nimg"; \
		printf "  $(G)ok$(X)      nothing left to do needs the internet\n"; \
	else \
		if [ -n "$$missingimg" ]; then \
			printf "  $(R)FAIL$(X)    %s image(s) not built or pulled yet:\n" "$$(echo $$missingimg | wc -w)"; \
			for m in $$missingimg; do printf "                %s\n" "$$m"; done; \
			printf "              Run $(C)make prepare$(X) — it pulls, builds and prepares the map.\n"; \
			printf "              That is the only step that needs the internet.\n"; \
			fail=1; \
		fi; \
		[ -s "$$graph" ] || printf "  $(Y)warn$(X)    the routable graph is not built yet — $(C)make lion-fetch && make lion-prepare$(X)\n"; \
		if [ ! -s "$$mbtiles" ]; then \
			printf "  $(R)FAIL$(X)    nyc.mbtiles is missing — Grafana maps 503/404 until $(C)make tiles-prepare$(X)\n"; \
			printf "              Once. make destroy keeps nus-tiles-data; make nuke is what deletes it.\n"; \
			fail=1; \
		fi; \
		probe=$$(grep -E '^BUSYBOX_IMAGE=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
		probe=$${probe:-busybox:1.38.0}; \
		docker image inspect "$$probe" >/dev/null 2>&1 || docker pull -q "$$probe" >/dev/null 2>&1 || true; \
		if ! docker image inspect "$$probe" >/dev/null 2>&1; then \
		printf "  $(Y)warn$(X)    could not fetch %s, so container networking was not checked\n" "$$probe"; \
		elif docker run --rm "$$probe" sh -c 'nslookup deb.debian.org >/dev/null 2>&1' >/dev/null 2>&1; then \
		if docker run --rm "$$probe" sh -c 'wget -q -T5 -O- http://deb.debian.org/ >/dev/null 2>&1' >/dev/null 2>&1; then \
			printf "  $(G)ok$(X)      containers can resolve DNS and reach the internet\n"; \
			elif [ "$$(grep -E '^BUILD_NETWORK=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-)" = host ]; then \
			printf "  $(Y)warn$(X)    containers cannot reach the internet, but BUILD_NETWORK=host is set,\n"; \
			printf "              so the image builds will use the host's network and succeed. Nothing\n"; \
			printf "              else needs container-side internet - lion-fetch/lion-prepare both run\n"; \
			printf "              on the host, and bootstrap only ever restores what they built.\n"; \
			else \
			printf "  $(R)FAIL$(X)    containers resolve DNS but cannot reach the internet.\n"; \
			printf "              Image pulls still work (the daemon fetches those over the host's\n"; \
			printf "              own stack), but every image built here installs packages, and that\n"; \
			printf "              runs inside a container.\n"; \
			printf "              FIRST, check the host. If it cannot reach them either, this is not\n"; \
			printf "              a Docker problem at all:\n"; \
			printf "                $(C)curl -sS -m10 -I http://deb.debian.org/ | head -1$(X)\n"; \
			printf "              Then read the symptom, which names the fault:\n"; \
			printf "                'Connection refused'     something is actively rejecting it —\n"; \
			printf "                                         upstream filtering or a transparent proxy\n"; \
			printf "                hangs, then times out    MTU mismatch, or a firewall DROP\n"; \
			printf "                'Network is unreachable' routing, or net.ipv4.ip_forward is 0\n"; \
			printf "              Reproduce it directly:\n"; \
			printf "                $(C)docker run --rm %s wget -O- -T8 http://deb.debian.org/$(X)\n" "$$probe"; \
			printf "              If the host CAN reach them and only containers cannot, its route out\n"; \
			printf "              captures traffic originating on the host but not traffic forwarded\n"; \
			printf "              from containers.\n"; \
			printf "              Then set $(C)BUILD_NETWORK=host$(X) in $(ENV_FILE) and re-run.\n"; \
			fail=1; \
			fi; \
		else \
		printf "  $(R)FAIL$(X)    containers cannot resolve DNS, so no image can be built.\n"; \
		printf "              Image pulls still work (the daemon uses the host's stack), so this\n"; \
		printf "              looks fine until the first build. Usual causes:\n"; \
		printf "                net.ipv4.ip_forward is 0   -> sysctl -w net.ipv4.ip_forward=1\n"; \
		printf "                iptables FORWARD drops     -> iptables -S FORWARD | head\n"; \
		printf "                no resolver in the container -> add \"dns\" to /etc/docker/daemon.json\n"; \
		printf "              Reproduce it directly:\n"; \
		printf "                $(C)docker run --rm %s nslookup deb.debian.org$(X)\n" "$$probe"; \
		fail=1; \
		fi; \
	fi; \
	$(COMPOSE) config -q 2>/dev/null \
		&& printf "  $(G)ok$(X)      all 14 components resolve from $(ENV_FILE)\n" \
		|| { printf "  $(R)FAIL$(X)    the compose model does not resolve:\n"; $(COMPOSE) config -q 2>&1 | sed 's/^/              /'; fail=1; }; \
	if command -v ss >/dev/null 2>&1; then listening=$$(ss -ltn 2>/dev/null | awk 'NR>1{print $$4}' || true); \
	elif command -v netstat >/dev/null 2>&1; then listening=$$(netstat -ltn 2>/dev/null | awk 'NR>2{print $$4}' || true); \
	else listening=""; fi; \
	if [ -z "$$listening" ]; then \
		printf "  $(Y)warn$(X)    no ss or netstat here, so host ports were not checked\n"; \
	else \
		ours=$$(docker ps --filter "label=com.docker.compose.project=not-uber-service" \
			--format '{{.Ports}}' 2>/dev/null | grep -oE '[0-9]+->' | tr -d '>-' | sort -u || true); \
		busy=""; mine=""; \
		for p in $$(grep -oE '^LB_[AB]_[A-Z_]+PORT=[0-9]+' $(ENV_FILE) 2>/dev/null | cut -d= -f2); do \
			echo "$$listening" | grep -qE "[:.]$$p\$$" || continue; \
			if echo "$$ours" | grep -qx "$$p"; then mine="$$mine $$p"; else busy="$$busy $$p"; fi; done; \
		if [ -n "$$busy" ]; then \
			printf "  $(R)FAIL$(X)    host ports held by something else:%s\n" "$$busy"; fail=1; \
			for p in $$busy; do \
				who=$$(ss -ltnp 2>/dev/null | grep -E "[:.]$$p[[:space:]]" \
					| grep -oE 'users:\(\("[^"]+"' | grep -oE '"[^"]+"' | tr -d '"' | sort -u | paste -sd, - || true); \
				ctr=$$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -E "[:.]$$p->" | cut -f1 | paste -sd, - || true); \
				if [ -n "$$ctr" ]; then label="container $$ctr"; \
				elif [ -n "$$who" ]; then label="host process $$who"; \
				else label="something this user cannot see — retry: sudo ss -ltnp | grep :$$p"; fi; \
				printf "            %-6s held by %s\n" "$$p" "$$label"; \
			done; \
			printf "            Either stop that, or change the LB_* port in $(ENV_FILE).\n"; \
		elif [ -n "$$mine" ]; then \
			printf "  $(G)ok$(X)      published ports are held by this stack's own containers (already up)\n"; \
		else \
			printf "  $(G)ok$(X)      every published host port is free\n"; \
		fi; \
	fi; \
	state=$$(grep -E '^ETCD_INITIAL_CLUSTER_STATE=' $(ETCD_ENV) | cut -d= -f2); \
	if docker volume inspect nus-etcd-data-1 >/dev/null 2>&1; then \
		[ "$$state" = existing ] && printf "  $(G)ok$(X)      etcd state 'existing' and the data volumes are there\n" \
			|| printf "  $(Y)warn$(X)    etcd volumes exist but state is '$$state' — run 'make etcd-existing' after this bring-up\n"; \
	else \
		[ "$$state" = new ] && printf "  $(G)ok$(X)      etcd state 'new' for a first bootstrap\n" \
			|| { printf "  $(R)FAIL$(X)    etcd state is '$$state' but there are no data volumes; it must be 'new' to bootstrap\n"; fail=1; }; \
	fi; \
	echo; \
	if [ "$$fail" -ne 0 ]; then printf "$(R)$(B)preflight failed — fix the above before deploying.$(X)\n\n"; exit 1; \
	else printf "$(G)$(B)preflight passed.$(X)\n\n"; fi


# Both init and up call this. After make destroy the tree is gone, and
# requiring a manual make init in between would be a trap: the error would
# arrive from preflight, several steps away from the cause.
.PHONY: _mk-data-tree
_mk-data-tree:
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	[ -n "$$root" ] || exit 0; \
	for d in $$($(COMPOSE) config 2>/dev/null | grep -oE 'device: .+' | cut -d' ' -f2- || true); do \
		mkdir -p "$$d"; done; \
	made=$$(find "$$root" -maxdepth 1 -mindepth 1 -type d -name 'nus-*' 2>/dev/null | wc -l); \
	free=$$(df -BG --output=avail "$$root" 2>/dev/null | tail -1 | tr -dc '0-9'); \
	printf "  $(G)ok$(X)      %s data directories under %s (%s GB free)\n" "$$made" "$$root" "$$free"

