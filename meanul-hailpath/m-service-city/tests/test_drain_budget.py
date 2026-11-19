"""City must drain Kafka faster than the fleet produces positions."""

from city_service.drain import (
    DRAIN_CAP_SECONDS,
    DRAIN_FLOOR_SECONDS,
    drain_budget_seconds,
)


def test_drain_budget_is_half_the_score_interval_until_the_cap():
    assert drain_budget_seconds(8.0) == 4.0
    assert drain_budget_seconds(30.0) == DRAIN_CAP_SECONDS
    assert drain_budget_seconds(0.2) == DRAIN_FLOOR_SECONDS
    assert drain_budget_seconds(30.0) < 30.0
