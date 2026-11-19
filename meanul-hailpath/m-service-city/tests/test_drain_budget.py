"""City must drain Kafka faster than the fleet produces positions."""

from city_service.drain import (
    DRAIN_CAP_SECONDS,
    DRAIN_FLOOR_SECONDS,
    DRAIN_IDLE_SECONDS,
    drain_budget_seconds,
    drain_queue_dry,
)


def test_drain_budget_is_half_the_score_interval_until_the_cap():
    assert drain_budget_seconds(8.0) == 4.0
    assert drain_budget_seconds(30.0) == DRAIN_CAP_SECONDS
    assert drain_budget_seconds(0.2) == DRAIN_FLOOR_SECONDS
    assert drain_budget_seconds(30.0) < 30.0


def test_drain_does_not_stop_on_the_first_empty_poll():
    assert drain_queue_dry(None, 1.0) is False
    assert drain_queue_dry(1.0, 1.0 + DRAIN_IDLE_SECONDS / 2) is False
    assert drain_queue_dry(1.0, 1.0 + DRAIN_IDLE_SECONDS) is True
