.PHONY: init
init:
	$(call say,Preparing the working copy)
# FORCE=1 regenerates .env from the template, which is the point: it is how a
# .env that predates a template change gets the new settings. The passwords go
# with it and have to be set again - that is what FORCE is for.
#
# The two answers below are different in kind. They are not template settings
# with a newer default; they are facts about THIS machine, and the template has
# no opinion worth restoring. Resetting HAILPATH_VOLUME_ROOT in particular does
# not just lose a value: every volume is a bind mount under it, so the next
# bring-up silently builds a second, empty tree somewhere else and leaves the
# real one on disk. So both are carried across from the backup.
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
		for key in HAILPATH_VOLUME_ROOT KAFKA_ADVERTISED_HOST; do \
			was=$$(grep -E "^$$key=" $(ENV_FILE).bak | tail -1 | cut -d= -f2-); \
			case "$$was" in ""|change-me*|/change-me*) continue;; esac; \
			sed -i "s|^$$key=.*|$$key=$$was|" $(ENV_FILE); \
			printf "  $(G)carried$(X) $$key=%s\n" "$$was"; \
		done; \
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
# Virtual and tunnel interfaces are excluded along with docker's own. A
# VPN usually holds the default route, so the device lookup below lands on
# the tunnel and advertises an address no client can reach - seen on a
# workstation where the default route was a nekoray-tun at 172.19.0.1,
# while the address a client would actually use was the wifi interface.
# libvirt's virbr* are out for the same reason: a host-local bridge is
# not how anything reaches this machine.
# Ports and passwords get a plain change-me placeholder above, same as any
# other setting — preflight already refuses to deploy on one. These two are
# the exception: a guess beats a placeholder, because this is the one
# setting no proxy can paper over if it's wrong (README: c-infra-kafka).
# Only touches a value still literally the placeholder, so a real answer
# already in .env — yours, or detected on an earlier run — is never
# silently redetected and never drifts on its own.
	@if grep -qE '^KAFKA_ADVERTISED_HOST=change-me' $(ENV_FILE) 2>/dev/null; then \
		def=$$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'dev [a-z0-9.-]+' | awk '{print $$2}' | head -1 || true); \
		ips=$$(ip -4 -o addr show scope global 2>/dev/null \
			| awk '{gsub(/\/.*/,"",$$4); print $$2, $$4}' \
			| grep -vE '^(docker[0-9]*|br-[0-9a-f]+|veth[0-9a-f]*|virbr[0-9]*|tun[0-9]*|tap[0-9]*|wg[0-9]*|[a-z0-9]+-tun[0-9]*) ' || true); \
		a=$$(echo "$$ips" | awk -v d="$$def" '$$1==d{print $$2; f=1} END{if(!f) exit 1}' \
			|| echo "$$ips" | head -1 | awk '{print $$2}'); \
		if [ -n "$$a" ]; then \
			sed -i "s/^KAFKA_ADVERTISED_HOST=.*/KAFKA_ADVERTISED_HOST=$$a/" $(ENV_FILE); \
			printf "  $(G)detected$(X) KAFKA_ADVERTISED_HOST=$$a\n"; \
			printf "  $(Y)This is a guess from this host's current interfaces — confirm it is\n"; \
			printf "  actually how a client will reach this host before deploying piece c.$(X)\n"; \
		else \
			printf "  $(Y)warn$(X)    no network interface found — edit KAFKA_ADVERTISED_HOST by hand\n"; \
		fi; \
	fi
# Same rule as KAFKA_ADVERTISED_HOST above: a guess beats a placeholder, and
# only a value still literally the placeholder is ever touched, so an answer
# already in .env - yours, or detected on an earlier run - is never
# overwritten. Overwriting it would be worse than useless here: every data
# volume is a bind mount under this path, so changing it orphans the lot.
#
# This one has no sensible shipped default at all. It used to be the deploy
# host's /data-root/lssd, which exists on exactly one machine, and make init
# then died on `cannot create` AFTER it had already written .env - so the run
# failed halfway and the obvious fix, running it again, failed the same way.
	@if grep -qE '^HAILPATH_VOLUME_ROOT=/?change-me' $(ENV_FILE) 2>/dev/null; then \
		root=""; \
		for cand in "$$HOME/.local/share/hailpath" "/var/lib/hailpath" "$(CURDIR)/.hailpath-data"; do \
			if mkdir -p "$$cand" 2>/dev/null; then root="$$cand"; break; fi; \
		done; \
		if [ -n "$$root" ]; then \
			sed -i "s|^HAILPATH_VOLUME_ROOT=.*|HAILPATH_VOLUME_ROOT=$$root|" $(ENV_FILE); \
			printf "  $(G)detected$(X) HAILPATH_VOLUME_ROOT=%s\n" "$$root"; \
			printf "  $(Y)Every data volume is a bind mount under this path. Point it at your\n"; \
			printf "  largest, fastest disk before deploying — moving it later orphans the tree.$(X)\n"; \
		else \
			printf "  $(R)FAIL$(X)    could not create a data root anywhere — set HAILPATH_VOLUME_ROOT by hand\n"; \
			exit 1; \
		fi; \
	fi
	@if docker network inspect $(NETWORK) >/dev/null 2>&1; then \
		printf "  $(G)ok$(X)      network $(NETWORK) exists\n"; \
	else \
		docker network create $(NETWORK) >/dev/null; \
		printf "  $(G)created$(X) network $(NETWORK)\n"; \
	fi
	@root=$$(grep -E '^HAILPATH_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -z "$$root" ]; then \
		printf "  $(R)FAIL$(X)    HAILPATH_VOLUME_ROOT is not set in $(ENV_FILE)\n"; exit 1; \
	elif ! mkdir -p "$$root" 2>/dev/null; then \
		printf "  $(R)FAIL$(X)    cannot create %s\n" "$$root"; \
		printf "              This is where every data volume lives, set as HAILPATH_VOLUME_ROOT\n"; \
		printf "              in $(ENV_FILE). Either the parent directory does not exist, or\n"; \
		printf "              you do not have permission to write there. Fix one of:\n"; \
		printf "                $(C)sudo mkdir -p %s && sudo chown $$(id -un) %s$(X)\n" "$$root" "$$root"; \
		printf "                or point HAILPATH_VOLUME_ROOT at a path you can write\n"; \
		exit 1; \
	fi
	@$(MAKE) --no-print-directory _mk-data-tree
	@printf "\n  Now edit $(B)$(ENV_FILE)$(X) — section 1 holds every password.\n"
	@printf "  Then, in order:\n"
	@printf "    $(C)make prepare$(X)    pull, build and prepare the map (needs the internet)\n"
	@printf "    $(C)make up$(X)         the bring-up (needs no internet at all)\n\n"

