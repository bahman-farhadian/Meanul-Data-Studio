"""How long city spends draining Kafka before the next hotspot publish."""

# A 5000-message cap fell behind 1060 drivers sending ~6700 positions per
# tick. Half the score interval, capped, empties the backlog without
# delaying hotspot publish forever.
DRAIN_CAP_SECONDS = 5.0
DRAIN_FLOOR_SECONDS = 0.5


def drain_budget_seconds(score_seconds: float) -> float:
    """How long to spend reading streams before the next score publish."""
    return max(DRAIN_FLOOR_SECONDS, min(float(score_seconds) * 0.5, DRAIN_CAP_SECONDS))
