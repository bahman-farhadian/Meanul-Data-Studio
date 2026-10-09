# The volumes are bind mounts, so `docker compose down -v` removes the volume
# entries and leaves every byte on disk. Nothing else deletes them, and a
# leftover tree is picked up by the next bring-up as if it were a fresh
# volume — a half-initialised PostgreSQL or etcd is far worse than none.
.PHONY: _rm-data-tree
_rm-data-tree:
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -z "$$root" ] || [ ! -d "$$root" ]; then \
		printf "  $(Y)skipped$(X) no data tree to remove\n"; \
	else \
		case "$$root" in /|/root|/home|/usr|/etc|/var|/boot|/data-root) \
			printf "  $(R)REFUSED$(X) NUS_VOLUME_ROOT is %s — too broad to delete\n" "$$root"; exit 1;; esac; \
		if [ "$(RM_MAP)" = 1 ]; then keep=""; else keep="nus-lion-data nus-tiles-data"; fi; \
		before=0; after=0; \
		for d in $$(find "$$root" -maxdepth 1 -mindepth 1 -type d -name 'nus-*' 2>/dev/null); do \
			base=$$(basename "$$d"); \
			[ -n "$$keep" ] && echo " $$keep " | grep -q " $$base " && continue; \
			before=$$((before+1)); rm -rf "$$d" 2>/dev/null || true; \
			[ -d "$$d" ] && after=$$((after+1)) || true; done; \
		[ -z "$$keep" ] && rmdir "$$root" 2>/dev/null || true; \
		if [ "$$after" -eq 0 ]; then \
			printf "  $(G)removed$(X) %s data directories under %s\n" "$$before" "$$root"; \
			for k in $$keep; do \
				[ -d "$$root/$$k" ] && \
					printf "  $(G)kept$(X)    %s (%s) — refetch is not needed on the next bootstrap\n" \
						"$$k" "$$(du -sh "$$root/$$k" 2>/dev/null | cut -f1)"; \
			done; \
		else \
			printf "  $(R)FAIL$(X)    %s of %s data directories could NOT be removed under %s\n" "$$after" "$$before" "$$root"; \
			printf "              The containers write as their own users, so these files are\n"; \
			printf "              not yours to delete. Re-run as root:\n"; \
			printf "                $(C)sudo make $(MAKECMDGOALS)$(X)\n"; \
			printf "              $(Y)Until they are gone the next 'make up' will find them and treat\n"; \
			printf "              them as a fresh volume — a half-initialised cluster.$(X)\n"; \
			exit 1; \
		fi; \
	fi

.PHONY: clean-images
clean-images:
	$(call say,Removing images)
	@built=$$($(COMPOSE) config --images 2>/dev/null | grep '^nus/' | sort -u); \
	pulled=$$($(COMPOSE) --profile init config --images 2>/dev/null | grep -v '^nus/' | sort -u); \
	bases=$$(grep -oE '^(PG|CONNECT|GRAFANA|SUPERSET|PYTHON|UV)_IMAGE=.+' $(ENV_FILE) 2>/dev/null | cut -d= -f2-); \
	for i in $$built $$pulled $$bases; do \
		if docker image inspect "$$i" >/dev/null 2>&1; then \
			docker rmi "$$i" >/dev/null 2>&1 \
				&& printf "  $(G)removed$(X) %s\n" "$$i" \
				|| printf "  $(Y)in use$(X)  %s (a container still references it)\n" "$$i"; \
		fi; \
	done
	@printf "  $(G)ok$(X)      build cache left alone; 'docker builder prune' clears that\n"

.PHONY: clean
clean:
	@printf "$(R)$(B)This removes everything this project put on the host:$(X)\n"
	@printf "  - every container, and the whole data tree under NUS_VOLUME_ROOT\n"
	@printf "    (databases, cache, topics, warehouse, and the downloaded street map)\n"
	@printf "  - every image it built and pulled\n"
	@printf "  - the $(NETWORK) network\n"
	@printf "  - your local .env, and the etcd state goes back to 'new'\n"
	@printf "\n  The repository itself is untouched, and nothing outside Docker is.\n\n"
	@read -r -p "Type 'clean' to confirm: " a; [ "$$a" = clean ] || { echo "cancelled"; exit 1; }
	@$(COMPOSE) --profile init down -v --remove-orphans 2>/dev/null || true
	$(call ok,containers and volume entries removed)
	@$(MAKE) --no-print-directory _rm-data-tree
	@$(MAKE) --no-print-directory clean-images
	@docker network rm $(NETWORK) >/dev/null 2>&1 && printf "  $(G)removed$(X) network $(NETWORK)\n" \
		|| printf "  $(Y)skipped$(X) network $(NETWORK) (already gone, or still in use)\n"
	@$(MAKE) --no-print-directory etcd-new >/dev/null
	$(call ok,etcd state back to 'new')
	@if [ -f $(ENV_FILE) ]; then mv $(ENV_FILE) $(ENV_FILE).removed; \
		printf "  $(G)moved$(X)   $(ENV_FILE) -> $(ENV_FILE).removed (your passwords, kept just in case)\n"; fi
	@printf "\n$(G)$(B)Clean.$(X) The host is as it was. 'make init' starts again from nothing.\n"
	@printf "  Docker's shared build cache is the one thing left: $(C)docker builder prune$(X)\n\n"
