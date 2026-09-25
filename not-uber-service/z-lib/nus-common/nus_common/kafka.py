"""Producing and consuming Avro messages on Kafka.

Every message in this stack is binary Avro. The message itself carries only
the id of its schema, and the Schema Registry says what that id means. This
module hides that exchange behind two small classes.

Where schemas come from: the `.avsc` files in `c-infra-kafka/schemas/`, which
each Python image copies in. A producer registers its schema the first time
it sends; a consumer never needs the file at all, because the id inside each
message is enough to look the schema up.
"""

import json
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer, AvroSerializer
from confluent_kafka.serialization import (
    MessageField,
    SerializationContext,
    StringDeserializer,
    StringSerializer,
)

from nus_common import config
from nus_common.logging import get_logger

log = get_logger(__name__)


def _bootstrap() -> str:
    return config.optional("KAFKA_BOOTSTRAP", "kafka-1:9092,kafka-2:9092,kafka-3:9092")


def _registry() -> SchemaRegistryClient:
    return SchemaRegistryClient(
        {"url": config.optional("SCHEMA_REGISTRY_URL", "http://schema-registry:8081")}
    )


def load_schema(topic: str) -> str:
    """Read the Avro schema file that belongs to a topic."""
    directory = Path(config.optional("SCHEMA_DIR", "/app/schemas"))
    path = directory / f"{topic}.avsc"
    if not path.exists():
        raise FileNotFoundError(
            f"no schema file for topic '{topic}' at {path}. The image should "
            f"copy c-infra-kafka/schemas into {directory}."
        )
    # Parsed and re-dumped so a broken file fails here, with the file name in
    # the error, instead of inside the serializer later.
    return json.dumps(json.loads(path.read_text()))


# Every record in c-infra-kafka/schemas carries these four. They are filled
# in here, in the one place every message in the stack passes through,
# rather than at each call site - a caller that forgets is the failure this
# design removes, and there is no reason to trust eight call sites with
# something one function can do.
ENVELOPE_VERSION = 1


class AvroTopicProducer:
    """Sends messages to one topic, encoded as Avro.

    Settings worth knowing:

    - `acks=all` with idempotence: a message is confirmed only once the
      brokers that must hold it do, and a retry cannot create a duplicate.
      Note what that does NOT cover: it makes the producer's own retry safe,
      not a re-send after the process restarted, and not a consumer reading
      the same offset twice. That is what event_id below is for.
    - `linger.ms`: wait a few milliseconds to fill a batch. It costs a little
      delay and saves a lot of network round trips at these message rates.
    """

    def __init__(self, topic: str, producer: str) -> None:
        """`producer` is the service name stamped on every message.

        Required rather than defaulted: "which service wrote this" is the
        first question asked when a stream looks wrong, and a default would
        quietly answer it incorrectly for whichever service forgot.
        """
        self.topic = topic
        self.producer_name = producer
        self._serializer = AvroSerializer(_registry(), load_schema(topic))
        self._key_serializer = StringSerializer("utf_8")
        self._producer = Producer(
            {
                "bootstrap.servers": _bootstrap(),
                "acks": "all",
                "enable.idempotence": True,
                "compression.type": "lz4",
                "linger.ms": config.integer("KAFKA_LINGER_MS", 20),
                "batch.size": 131072,
                # If the brokers cannot keep up, block the producer rather
                # than dropping messages or growing memory without limit.
                "queue.buffering.max.messages": 200000,
            }
        )
        self._pending_errors: list[str] = []

    def _on_delivery(self, err: KafkaError | None, message: Message) -> None:
        """Called once per message, after the brokers answered."""
        if err is not None:
            self._pending_errors.append(str(err))
            log.error(
                "message not delivered",
                extra={"topic": self.topic, "error": str(err)},
            )

    def send(self, key: str, value: dict, correlation_id: str | None = None) -> None:
        """Queue one message. Delivery happens in the background.

        The key decides the partition, and therefore the order: everything
        with the same key stays in the order it was sent.

        `correlation_id` ties every message about one trip together across
        topics - pass the trip_id on anything trip-scoped. It is a separate
        argument rather than a field the caller puts in `value` because it
        is the one piece of the envelope only the caller knows.
        """
        context = SerializationContext(self.topic, MessageField.VALUE)
        self._producer.produce(
            topic=self.topic,
            key=self._key_serializer(key),
            value=self._serializer(self._stamped(value, correlation_id), context),
            on_delivery=self._on_delivery,
        )
        # Give the background thread a chance to run its callbacks. Without
        # this the delivery reports only arrive at flush time.
        self._producer.poll(0)

    def _stamped(self, value: dict, correlation_id: str | None) -> dict:
        """The caller's payload with the envelope filled in around it.

        A fresh event_id per call, which is the whole point: it is what lets
        clickhouse-sink tell a replayed batch from a genuine repeat.
        ClickHouse will not do that for us - ReplacingMergeTree collapses
        only eventually, only within a partition, and only on merge - so
        uniqueness has to be true before the warehouse, not after it.

        The caller's own keys win, so a producer that has a real event_id
        already (a replay of a stored event, a test with a fixed id) can
        pass it and keep it.
        """
        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_version": ENVELOPE_VERSION,
            "producer": self.producer_name,
            "correlation_id": correlation_id,
        }
        return {**envelope, **value}

    def flush(self, timeout_seconds: float = 10.0) -> int:
        """Wait for queued messages to be delivered.

        Returns how many were still unsent when the wait ran out - a number
        above zero means the brokers are not keeping up. Always call this
        before exiting, or the last messages are lost.
        """
        remaining = self._producer.flush(timeout_seconds)
        if remaining:
            log.warning(
                "messages still queued after flush",
                extra={"topic": self.topic, "remaining": remaining},
            )
        return remaining


class AvroTopicConsumer:
    """Reads messages from one or more topics, decoded from Avro.

    Positions are committed by hand, after the message has been dealt with.
    Automatic commits would mark a message as done the moment it was read,
    so a crash in the middle of handling it would lose it silently.
    """

    def __init__(
        self,
        topics: list[str],
        group_id: str,
        from_beginning: bool = True,
        avro_keys: bool = False,
    ) -> None:
        """`avro_keys` matters for the cdc.* topics.

        The stack's own producers use a plain text key - a driver id, a trip
        id. Debezium does not: its key is the row's primary key, encoded as
        Avro like the value. Reading one with the other's decoder fails, so
        the consumer has to be told which kind it is about to read.
        """
        self.topics = topics
        # A regex subscription matches nothing until the topics exist, and
        # librdkafka reports that as UNKNOWN_TOPIC_OR_PART rather than as
        # an empty assignment. For cache-updater's "^cdc\\..*" that is the
        # normal startup order - Debezium creates those topics only once
        # its connector registers - and raising it cost five crash-restarts
        # and about five minutes on every bring-up, which every service
        # waiting on the cache then inherited. A literal topic that does
        # not exist is still an error worth raising: that is a typo, not a
        # race.
        self._pattern_subscription = any(name.startswith("^") for name in topics)
        self._missing_topics_logged = False
        self._deserializer = AvroDeserializer(_registry())
        self._key_deserializer = (
            AvroDeserializer(_registry()) if avro_keys else StringDeserializer("utf_8")
        )
        self._avro_keys = avro_keys
        self._consumer = Consumer(
            {
                "bootstrap.servers": _bootstrap(),
                "group.id": group_id,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest" if from_beginning else "latest",
                # A consumer that stops answering is removed from the group
                # after this long, and its partitions go to somebody else.
                "session.timeout.ms": 45000,
                "max.poll.interval.ms": 300000,
            }
        )
        self._consumer.subscribe(topics)
        log.info("consumer subscribed", extra={"topics": topics, "group": group_id})

    def messages(self, should_stop: Callable[[], bool], timeout: float = 1.0) -> Iterator[tuple]:
        """Yield (topic, key, value) until asked to stop.

        A message with no value is a tombstone - the marker Debezium leaves
        behind after a delete - and is passed on as value None so the caller
        can act on it.
        """
        while not should_stop():
            message = self._consumer.poll(timeout)
            if message is None:
                continue
            if message.error():
                if self._tolerable(message.error()):
                    continue
                raise KafkaException(message.error())

            topic = message.topic()
            key = self._decode_key(topic, message.key())

            raw_value = message.value()
            if raw_value is None:
                value = None
            else:
                context = SerializationContext(topic, MessageField.VALUE)
                value = self._deserializer(raw_value, context)

            yield topic, key, value

    def _tolerable(self, error: KafkaError) -> bool:
        """True for the conditions that are news, not failures."""
        # The end of a partition is normal news, not a problem.
        if error.code() == KafkaError._PARTITION_EOF:
            return True
        if self._pattern_subscription and error.code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
            if not self._missing_topics_logged:
                # Once, not per poll: this repeats several times a second
                # until the topics appear, and a log full of it would bury
                # whatever is actually wrong.
                log.info(
                    "waiting for topics to appear",
                    extra={"pattern": self.topics},
                )
                self._missing_topics_logged = True
            return True
        return False

    def _decode_key(self, topic: str, raw_key: bytes | None):
        """Turn the message key back into something readable."""
        if raw_key is None:
            return None
        if self._avro_keys:
            # An Avro decoder needs to know which topic and which half of the
            # message it is reading; a text decoder does not.
            return self._key_deserializer(
                raw_key, SerializationContext(topic, MessageField.KEY)
            )
        return self._key_deserializer(raw_key)

    def poll_once(self, timeout: float = 0.0) -> tuple | None:
        """Read at most one message and return at once.

        For services that both produce and consume: they cannot sit in a
        blocking read, because they also have their own work to do on every
        tick of their loop.
        """
        message = self._consumer.poll(timeout)
        if message is None:
            return None
        if message.error():
            if self._tolerable(message.error()):
                return None
            raise KafkaException(message.error())

        topic = message.topic()
        key = self._decode_key(topic, message.key())
        raw_value = message.value()
        if raw_value is None:
            return topic, key, None
        context = SerializationContext(topic, MessageField.VALUE)
        return topic, key, self._deserializer(raw_value, context)

    def commit(self) -> None:
        """Record how far this consumer has got.

        Called after a batch has been handled, so a restart repeats at most
        that batch instead of losing it.

        A commit with nothing new to record is not an error. librdkafka
        answers that with _NO_OFFSET, and raising it here was doing real
        damage in city-service: the commit sits mid-tick, so the exception
        skipped the rest of the tick every time a tick happened to consume
        nothing. The traffic update lives after it and therefore never ran
        at all - segment_traffic_updates held zero messages on a stack that
        had been up for hours - and the "when did I last publish" bookkeeping
        after it never ran either, so scores republished on every tick
        instead of every thirty seconds. Both were silent; the only visible
        symptom was a log line saying the tick failed, which it had, for a
        reason that was not a failure.
        """
        try:
            self._consumer.commit(asynchronous=False)
        except KafkaException as err:
            if err.args[0].code() == KafkaError._NO_OFFSET:
                # Nothing consumed since the last commit. Normal on a quiet
                # topic, and on a service that commits on a timer rather
                # than per batch.
                return
            raise

    def close(self) -> None:
        """Leave the group cleanly, so the partitions move on at once."""
        self._consumer.close()
