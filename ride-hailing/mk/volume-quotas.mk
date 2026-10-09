# A real host-level operation, not something running inside a container can
# do: XFS project quotas are set via ioctls against the filesystem itself,
# and need root regardless of which namespace asks. Run this once, directly
# on the host - it persists across make destroy the same way the data
# directories themselves do; make nuke is the only thing that would need it
# run again, on a fresh disk.
#
# Sized as a safety NET, not a precise capacity plan: driver_location's
# Kafka retention and driver_positions' ClickHouse TTL are both already cut
# down for real fleet scale (see c-infra-kafka/topics/topics.tsv and
# e-infra-clickhouse/ddl/002_positions.sql), and these limits sit generously
# above what that sizing implies - close to it, not exact, since the actual
# per-message byte sizes here are estimated, not measured. Postgres's own
# trips table has no retention/archival at all and grows for as long as the
# stack runs live - these limits catch that before it takes the whole disk
# down with it, at the cost of dispatch-service seeing write failures once
# hit, rather than solving the underlying growth. Re-check real usage
# (xfs_quota -x -c 'report -p' <mount>) after a real run and adjust.
.PHONY: volume-quotas
volume-quotas:
	$(call say,Setting XFS project quotas on the data directories)
	@root=$$(grep -E '^NUS_VOLUME_ROOT=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -z "$$root" ] || [ ! -d "$$root" ]; then \
		printf "  $(R)FAIL$(X)    NUS_VOLUME_ROOT is not set or does not exist\n"; exit 1; \
	fi; \
	if ! command -v xfs_quota >/dev/null 2>&1; then \
		printf "  $(R)FAIL$(X)    xfs_quota not found - install xfsprogs on the host\n"; exit 1; \
	fi; \
	if [ "$$(id -u)" != 0 ]; then \
		printf "  $(R)FAIL$(X)    setting a quota LIMIT (not just the project association) needs\n"; \
		printf "              root - re-run as: $(C)sudo make volume-quotas$(X)\n"; exit 1; \
	fi; \
	mount=$$(df --output=target "$$root" | tail -1); \
	if ! grep -qE "^[^ ]+ $$mount xfs .*(pquota|prjquota)" /proc/mounts; then \
		printf "  $(R)FAIL$(X)    %s is not mounted with pquota/prjquota - quotas cannot be set\n" "$$mount"; \
		printf "              (see /etc/fstab; this host's own already has it - check NUS_VOLUME_ROOT\n"; \
		printf "              actually resolves to that mount)\n"; exit 1; \
	fi; \
	projid=100; \
	for entry in \
		"nus-kafka-data-1:96g" \
		"nus-ch-data-s1r1:112g" \
		"nus-pgdata-1:24g"; \
	do \
		dir=$${entry%%:*}; size=$${entry##*:}; path="$$root/$$dir"; \
		mkdir -p "$$path"; \
		xfs_quota -x -c "project -s -p $$path $$projid" "$$mount" >/dev/null; \
		xfs_quota -x -c "limit -p bhard=$$size $$projid" "$$mount"; \
		printf "  $(G)ok$(X)      %-20s hard limit %-6s (project %s)\n" "$$dir" "$$size" "$$projid"; \
		projid=$$((projid + 1)); \
	done; \
	printf "\n  Check any time with: $(C)sudo xfs_quota -x -c 'report -p' %s$(X)\n" "$$mount"

