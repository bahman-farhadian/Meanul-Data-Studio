.PHONY: init
init:
	$(call say,Preparing the working copy)
	@if [ -f $(ENV_FILE) ] && [ -z "$(FORCE)" ]; then \
		printf "  $(Y)kept$(X)    $(ENV_FILE) already exists — not overwriting it\n"; \
		if ! grep -qE '^PG_SUPERUSER_PASSWORD=' $(ENV_FILE); then \
			printf "  $(R)but$(X)     it predates the master settings file: it has no passwords in it.\n"; \
			printf "              Every component now resolves from this one file, so it needs\n"; \
			printf "              the full template. Back it up and regenerate:\n"; \
			printf "                $(C)cp $(ENV_FILE) $(ENV_FILE).bak && make init FORCE=1$(X)\n"; \
		fi; \
	elif [ -f $(ENV_FILE) ]; then \
		cp $(ENV_FILE) $(ENV_FILE).bak; \
		cp $(EXAMPLE) $(ENV_FILE); \
		printf "  $(G)created$(X) $(ENV_FILE) from $(EXAMPLE) (previous kept as $(ENV_FILE).bak)\n"; \
	else \
		cp $(EXAMPLE) $(ENV_FILE); \
		printf "  $(G)created$(X) $(ENV_FILE) from $(EXAMPLE)\n"; \
	fi
# An existing .env only ever gains lines here — nothing already set is ever
# touched. Runs every time, on a brand-new .env and a years-old one alike:
# a var this template defines but an existing .env predates is exactly the
# error class that sent someone chasing "required variable ... is missing
# a value" through six unrelated targets instead of getting told once, at
# the one command meant to catch it. Idempotent — a second run finds
# nothing missing and says so.
	@missing=""; \
	for key in $$(grep -oE '^[A-Z_][A-Z0-9_]*=' $(EXAMPLE) | tr -d '='); do \
		grep -q "^$$key=" $(ENV_FILE) || { \
			grep -E "^$$key=" $(EXAMPLE) | tail -1 >> $(ENV_FILE); \
			missing="$$missing $$key"; \
		}; \
	done; \
	if [ -n "$$missing" ]; then \
		printf "  $(G)added$(X)   not in your $(ENV_FILE) yet, pulled in from $(EXAMPLE):%s\n" "$$missing"; \
	else \
		printf "  $(G)ok$(X)      every setting the template defines is already present\n"; \
	fi
# Ports and passwords get a plain change-me placeholder above, same as any
# other setting — preflight already refuses to deploy on one. These two are
# the exception: a guess beats a placeholder, because this is the one
# setting no proxy can paper over if it's wrong (README: c-infra-kafka).
# Only touches a value still literally the placeholder, so a real answer
# already in .env — yours, or detected on an earlier run — is never
# silently redetected and never drifts on its own.
	@if grep -qE '^KAFKA_ADVERTISED_HOST_A=change-me' $(ENV_FILE) 2>/dev/null \
		|| grep -qE '^KAFKA_ADVERTISED_HOST_B=change-me' $(ENV_FILE) 2>/dev/null; then \
		def=$$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'dev [a-z0-9.-]+' | awk '{print $$2}' | head -1 || true); \
		ips=$$(ip -4 -o addr show scope global 2>/dev/null \
			| awk '{gsub(/\/.*/,"",$$4); print $$2, $$4}' \
			| grep -vE '^(docker[0-9]*|br-[0-9a-f]+|veth[0-9a-f]*) ' || true); \
		a=$$(echo "$$ips" | awk -v d="$$def" '$$1==d{print $$2; f=1} END{if(!f) exit 1}' \
			|| echo "$$ips" | head -1 | awk '{print $$2}'); \
		b=$$(echo "$$ips" | awk -v skip="$$a" '$$2!=skip{print $$2; exit}'); \
		if grep -qE '^KAFKA_ADVERTISED_HOST_A=change-me' $(ENV_FILE) && [ -n "$$a" ]; then \
			sed -i "s/^KAFKA_ADVERTISED_HOST_A=.*/KAFKA_ADVERTISED_HOST_A=$$a/" $(ENV_FILE); \
			printf "  $(G)detected$(X) KAFKA_ADVERTISED_HOST_A=$$a\n"; \
		fi; \
		if grep -qE '^KAFKA_ADVERTISED_HOST_B=change-me' $(ENV_FILE); then \
			bb=$${b:-$$a}; \
			if [ -n "$$bb" ]; then \
				sed -i "s/^KAFKA_ADVERTISED_HOST_B=.*/KAFKA_ADVERTISED_HOST_B=$$bb/" $(ENV_FILE); \
				printf "  $(G)detected$(X) KAFKA_ADVERTISED_HOST_B=$$bb\n"; \
				[ -z "$$b" ] && printf "  $(Y)note$(X)    only one network path found — B set the same as A\n"; \
			else \
				printf "  $(Y)warn$(X)    no network interface found — edit KAFKA_ADVERTISED_HOST_A/B by hand\n"; \
			fi; \
		fi; \
		printf "  $(Y)Both are a guess from this host's current interfaces — confirm they are\n"; \
		printf "  actually how a client will reach this host before deploying piece c.$(X)\n"; \
	fi
	@if docker network inspect $(NETWORK) >/dev/null 2>&1; then \
		printf "  $(G)ok$(X)      network $(NETWORK) exists\n"; \
	else \
		docker network create $(NETWORK) >/dev/null; \
		printf "  $(G)created$(X) network $(NETWORK)\n"; \
	fi
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -z "$$root" ]; then \
		printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT is not set in $(ENV_FILE)\n"; exit 1; \
	elif ! mkdir -p "$$root" 2>/dev/null; then \
		printf "  $(R)FAIL$(X)    cannot create %s\n" "$$root"; \
		printf "              This is where all 26 data volumes live, set as NUS_VOLUME_ROOT\n"; \
		printf "              in $(ENV_FILE). Either the parent directory does not exist, or\n"; \
		printf "              you do not have permission to write there. Fix one of:\n"; \
		printf "                $(C)sudo mkdir -p %s && sudo chown $$(id -un) %s$(X)\n" "$$root" "$$root"; \
		printf "                or point NUS_VOLUME_ROOT at a path you can write\n"; \
		exit 1; \
	fi
	@$(MAKE) --no-print-directory _mk-data-tree
	@printf "\n  Now edit $(B)$(ENV_FILE)$(X) — section 1 holds every password.\n"
	@printf "  Then, in order:\n"
	@printf "    $(C)make prepare$(X)    pull, build and prepare the map (needs the internet)\n"
	@printf "    $(C)make up$(X)         the bring-up (needs no internet at all)\n\n"

