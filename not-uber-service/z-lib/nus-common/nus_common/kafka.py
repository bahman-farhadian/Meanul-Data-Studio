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


class AvroTopicProducer:
    """Sends messages to one topic, encoded as Avro.

    Settings worth knowing:

    - `acks=all` with idempotence: a message is confirmed only once the
      brokers that must hold it do, and a retry cannot create a duplicate.
    - `linger.ms`: wait a few milliseconds to fill a batch. It costs a little
      delay and saves a lot of network round trips at these message rates.
    """

    def __init__(self, topic: str) -> None:
        self.topic = topic
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

    def send(self, key: str, value: dict) -> None:
        """Queue one message. Delivery happens in the background.

        The key decides the partition, and therefore the order: everything
        with the same key stays in the order it was sent.
        """
        context = SerializationContext(self.topic, MessageField.VALUE)
        self._producer.produce(
            topic=self.topic,
            key=self._key_serializer(key),
            value=self._serializer(value, context),
            on_delivery=self._on_delivery,
        )
        # Give the background thread a chance to run its callbacks. Without
        # this the delivery reports only arrive at flush time.
        self._producer.poll(0)

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

    def __init__(self, topics: list[str], group_id: str, from_beginning: bool = True) -> None:
        self.topics = topics
        self._deserializer = AvroDeserializer(_registry())
        self._key_deserializer = StringDeserializer("utf_8")
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
                # The end of a partition is normal news, not a problem.
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(message.error())

            topic = message.topic()
            key = self._key_deserializer(message.key()) if message.key() else None

            raw_value = message.value()
            if raw_value is None:
                value = None
            else:
                context = SerializationContext(topic, MessageField.VALUE)
                value = self._deserializer(raw_value, context)

            yield topic, key, value

    def commit(self) -> None:
        """Record how far this consumer has got.

        Called after a batch has been handled, so a restart repeats at most
        that batch instead of losing it.
        """
        self._consumer.commit(asynchronous=False)

    def close(self) -> None:
        """Leave the group cleanly, so the partitions move on at once."""
        self._consumer.close()
