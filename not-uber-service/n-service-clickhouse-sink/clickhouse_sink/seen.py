"""Refusing an event this sink has already written.

ClickHouse does not deduplicate for us. ReplacingMergeTree collapses only
eventually, only within a partition, and only on merge, and
`insert_deduplication_token` covers an identical retried block - none of
which is a foundation for a count a dashboard is allowed to show. So
uniqueness has to be true before the warehouse, which is what the
`event_id` on every message exists for.

What this actually protects against, stated exactly rather than generously:
offsets are committed after a batch is written, so a crash between the
write and the commit replays that batch. The replay window is therefore
bounded by how much can be in flight - the batch size, plus whatever the
consumer had read ahead. A bounded memory of recent ids covers that window
completely and costs nothing.

What it does NOT do is dedupe across a restart (the memory is empty again)
or across two sink instances (each has its own). Both are deliberate: the
first is covered because a restart re-reads from the committed offset, and
the second because this sink is a single consumer group member by design.
A duplicate that somehow survives all of that still meets trip_facts'
ReplacingMergeTree, which is the second net rather than the first.
"""

from __future__ import annotations

from collections import OrderedDict


class SeenEvents:
    """The ids written recently, oldest evicted first.

    Sized in events, not bytes. The default holds far more than the largest
    replayable window, which is what makes it a guarantee rather than a
    hope - see the module docstring for why that window is bounded.
    """

    def __init__(self, capacity: int = 500_000) -> None:
        assert capacity > 0, capacity
        self.capacity = capacity
        self._ids: OrderedDict[str, None] = OrderedDict()
        self.rejected = 0

    def accept(self, event_id: str | None) -> bool:
        """True when this event is new and should be written.

        A message with no event_id is accepted rather than dropped. Losing
        real data because a producer is on an older schema is the worse
        failure of the two, and the quality bar that counts null event_ids
        is where that shows up loudly instead of silently.
        """
        if not event_id:
            return True
        if event_id in self._ids:
            self.rejected += 1
            return False
        self._ids[event_id] = None
        if len(self._ids) > self.capacity:
            self._ids.popitem(last=False)
        return True

    def __len__(self) -> int:
        return len(self._ids)
