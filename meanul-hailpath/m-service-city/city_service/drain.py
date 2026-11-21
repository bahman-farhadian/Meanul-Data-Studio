"""How long city spends draining Kafka before the next hotspot publish."""

# Half the score interval, capped. City no longer drains driver_location;
# trip_lifecycle is small. The budget still bounds the Kafka read so a
# stall cannot delay hotspot publish forever.
DRAIN_CAP_SECONDS = 5.0
DRAIN_FLOOR_SECONDS = 0.5
# poll_once(0) returns None when the local buffer is empty even if the
# broker still has lag. Two empty polls ~0.3s apart means the queue is dry.
DRAIN_IDLE_SECONDS = 0.3


def drain_budget_seconds(score_seconds: float) -> float:
    """How long to spend reading streams before the next score publish."""
    return max(DRAIN_FLOOR_SECONDS, min(float(score_seconds) * 0.5, DRAIN_CAP_SECONDS))


def drain_queue_dry(empty_since: float | None, now: float) -> bool:
    """True after a short run of empty polls, not after the first None."""
    return empty_since is not None and (now - empty_since) >= DRAIN_IDLE_SECONDS
