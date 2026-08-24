"""What the city looks like right now, counted per zone.

The service listens to three streams and keeps a small running picture of
each zone in memory: how many riders are waiting, how many drivers are free,
and how fast cars are actually moving. Everything it publishes is worked out
from those three numbers.

Nothing is stored anywhere while it is being counted. If the service
restarts, the picture rebuilds itself within a minute from the streams, which
is far simpler than keeping a durable copy of something that is only ever a
few seconds old.
"""

from dataclasses import dataclass, field

# Speed a car makes in this city with nothing in the way. The same number
# h-bootstrap used, so the live traffic factors continue the seeded ones
# instead of stepping away from them.
FREE_FLOW_KMH = 26.0


@dataclass
class ZoneCounter:
    """The running picture of one zone."""

    # Trips whose pickup is in this zone and which have not been given a
    # driver yet. This is the demand side.
    waiting_riders: set[str] = field(default_factory=set)
    # Drivers seen free in this zone recently. The supply side.
    free_drivers: set[str] = field(default_factory=set)
    # Speeds reported by cars on a trip inside this zone.
    speed_total: float = 0.0
    speed_samples: int = 0

    def note_speed(self, kmh: float) -> None:
        # Zero speeds are dropped: a car at a red light says nothing about
        # how congested the road is, and enough of them would make every
        # street look impassable.
        if kmh > 1.0:
            self.speed_total += kmh
            self.speed_samples += 1

    @property
    def average_speed_kmh(self) -> float | None:
        if self.speed_samples == 0:
            return None
        return self.speed_total / self.speed_samples

    def demand_score(self) -> float:
        """How busy this zone is, from 0.0 to 1.0.

        Riders waiting against drivers free. The +1 keeps the arithmetic
        working when a zone has neither, and means one rider with no drivers
        gives a high score rather than an impossible one.
        """
        waiting = len(self.waiting_riders)
        free = len(self.free_drivers)
        if waiting == 0:
            return 0.0
        return round(min(waiting / (waiting + free + 1.0), 1.0), 3)

    def congestion_factor(self) -> float | None:
        """How much slower than free flow this zone is right now.

        1.0 means free flowing, 2.0 means everything takes twice as long.
        None when no car reported a speed, in which case the existing factor
        is left alone rather than replaced with a guess.
        """
        speed = self.average_speed_kmh
        if speed is None:
            return None
        # Kept inside sensible limits: one unusually fast or slow car should
        # not be able to tell the router that a street is impassable.
        return round(min(max(FREE_FLOW_KMH / speed, 0.6), 3.0), 3)

    def reset_speeds(self) -> None:
        """Forget the speed samples, keeping the rider and driver picture."""
        self.speed_total = 0.0
        self.speed_samples = 0


def surge_from_score(score: float) -> float:
    """Turn a demand score into a price multiplier.

    The same shape dispatch and bootstrap use: nothing until demand is
    clearly above normal, then a gentle rise, and a hard stop at 2.5.
    """
    if score <= 0.6:
        return 1.0
    return round(min(1.0 + (score - 0.6) * 1.6, 2.5), 2)
