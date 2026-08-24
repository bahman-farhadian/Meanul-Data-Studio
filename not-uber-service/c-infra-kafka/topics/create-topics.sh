#!/bin/sh
# Create every topic listed in topics.tsv.
#
# Safe to run again at any time: a topic that already exists is left exactly
# as it is. That makes this the normal way to roll out a newly added topic -
# add the line to topics.tsv and run the one-shot again.
#
# What it does NOT do: change an existing topic. Kafka cannot reduce the
# number of partitions of a live topic, so a change of that kind is a
# deliberate manual step, not something a script should do behind your back.
set -eu

BOOTSTRAP="${KAFKA_BOOTSTRAP:-kafka-1:9092}"
RETENTION_MS=$(( ${KAFKA_RETENTION_HOURS:-48} * 3600 * 1000 ))
KAFKA_TOPICS=/opt/kafka/bin/kafka-topics.sh
TAB=$(printf '\t')

echo "waiting for the brokers at ${BOOTSTRAP} ..."
attempt=1
while [ "$attempt" -le 30 ]; do
    if /opt/kafka/bin/kafka-broker-api-versions.sh \
        --bootstrap-server "$BOOTSTRAP" >/dev/null 2>&1; then
        break
    fi
    echo "  not ready yet (attempt ${attempt}/30)"
    attempt=$(( attempt + 1 ))
    sleep 5
done
if [ "$attempt" -gt 30 ]; then
    echo "brokers never answered - is the cluster up?" >&2
    exit 1
fi
echo "brokers are answering"

existing=$("$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" --list)

# IFS is set to a tab so the columns of topics.tsv are read as they are
# written. Lines starting with # and empty lines are skipped.
while IFS="$TAB" read -r name partitions key purpose; do
    case "$name" in
        ''|\#*) continue ;;
    esac

    if echo "$existing" | grep -qx "$name"; then
        echo "= ${name} already exists, left untouched"
        continue
    fi

    echo "+ creating ${name} (${partitions} partitions, keyed by ${key}) - ${purpose}"
    "$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" \
        --create --if-not-exists \
        --topic "$name" \
        --partitions "$partitions" \
        --replication-factor 3 \
        --config min.insync.replicas=2 \
        --config retention.ms="$RETENTION_MS"
done < /topics.tsv

echo
echo "topics now on the cluster:"
"$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" --list
