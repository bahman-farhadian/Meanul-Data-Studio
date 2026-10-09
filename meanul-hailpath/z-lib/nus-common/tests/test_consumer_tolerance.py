"""Which Kafka conditions are news, and which are real failures.

Two startup races have now taken production behaviour down by being
raised as errors when they are neither surprising nor harmful:

- _NO_OFFSET from a commit with nothing new to record. city-service
  commits on a timer, so this fires on any quiet tick; the raise skipped
  the rest of the tick, which is where the traffic update lived.
  segment_traffic_updates held zero messages for the life of the stack.
- UNKNOWN_TOPIC_OR_PART from a regex subscription whose topics do not
  exist yet. cache-updater subscribes to "^cdc\\..*" and Debezium creates
  those topics only after its connector registers, so this is the normal
  order of events. It cost five crash-restarts and about five minutes on
  every bring-up, and every service waiting on the cache inherited the
  delay.

What must still raise: a literal topic that does not exist. That is a
typo, not a race, and swallowing it would mean a consumer that reads
nothing forever while looking healthy.
"""

from __future__ import annotations

import sys
from pathlib import Path

NUS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(NUS / "z-lib" / "nus-common"))

from confluent_kafka import KafkaError  # noqa: E402

from nus_common.kafka import AvroTopicConsumer  # noqa: E402


class _Bare:
    """An AvroTopicConsumer without the Kafka and Registry connections.

    Only _tolerable is under test, and it depends on nothing but the
    subscription shape - so the object is built without __init__ rather
    than by standing up a broker for a pure decision function.
    """

    def __new__(cls, topics: list[str]):
        consumer = object.__new__(AvroTopicConsumer)
        consumer.topics = topics
        consumer._pattern_subscription = any(t.startswith("^") for t in topics)
        consumer._missing_topics_logged = False
        return consumer


def test_partition_eof_is_news_on_any_subscription():
    for topics in (["trip_lifecycle"], ["^cdc\\..*"]):
        consumer = _Bare(topics)
        assert consumer._tolerable(KafkaError(KafkaError._PARTITION_EOF))


def test_a_pattern_waits_for_topics_that_do_not_exist_yet():
    consumer = _Bare(["^cdc\\..*"])
    assert consumer._tolerable(KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART))


def test_a_literal_topic_that_does_not_exist_still_raises():
    """A typo must not look like a race."""
    consumer = _Bare(["trip_lifecycle"])
    assert not consumer._tolerable(KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART))


def test_a_real_broker_failure_still_raises_on_a_pattern():
    consumer = _Bare(["^cdc\\..*"])
    assert not consumer._tolerable(KafkaError(KafkaError.BROKER_NOT_AVAILABLE))


def test_the_waiting_message_is_logged_once_not_per_poll():
    consumer = _Bare(["^cdc\\..*"])
    for _ in range(50):
        consumer._tolerable(KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART))
    assert consumer._missing_topics_logged is True
