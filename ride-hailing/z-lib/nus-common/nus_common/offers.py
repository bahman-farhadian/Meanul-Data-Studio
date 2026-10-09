"""Offering a ride to a driver, who may say no.

Shared rather than owned by dispatch-service, because h-bootstrap seeds
the same funnel for the historical week. Two implementations of the
acceptance model would drift, and the seeded history would stop matching
the live traffic it is meant to be indistinguishable from.

Dispatch used to pick the nearest free driver and declare the trip matched.
No real platform works that way: it offers the ride to one candidate with a
deadline, and on a decline or a timeout the request goes to the next driver.
The studied platforms give a driver on the order of twenty seconds to
answer, and driver response rate is one of the numbers a marketplace is run
on - none of which was observable here, because refusal was not possible.

Two findings from the ride-sourcing literature shape `accept_chance` below,
and they are the reason this is worth simulating rather than coin-flipping:

- pickup time depresses acceptance - a driver twelve minutes away is far
  less likely to take the ride than one four minutes away;
- surge raises it, and is valued more highly than the trip fare itself.

A flat probability would fill dispatch_offers with rows and teach nobody
anything. This produces a funnel where accepted offers really do have
shorter ETAs than offered ones, which is a relationship worth charting and
a check that the simulation is behaving.

On timing: the chain resolves inside one dispatch tick rather than across
wall-clock ticks, because dispatch is a synchronous tick loop and turning
it into an asynchronous offer state machine would be a rewrite of the one
service that owns trip status. The simulated response times are real
numbers laid out on a real clock - the chain ENDS at the tick's `now` and
runs backwards from it, never forwards, so matched_at stays where it was
and no milestone lands in the future.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

# How long a driver has to answer. Around twenty seconds in the platforms
# the dispatch literature measured.
OFFER_TIMEOUT_S = 20

# A driver with nothing against the ride takes it this often. Real published
# acceptance rates sit broadly in the 50-70% band, and this is the value
# before ETA and surge move it.
BASE_ACCEPT_CHANCE = 0.62

# The ETA at which the pickup drive has applied its full penalty. Ten
# minutes: beyond that the marginal discouragement flattens out, which is
# why this is a saturating curve and not a straight line.
ETA_REFERENCE_S = 600.0
ETA_WEIGHT = 0.60

# How much a multiplier of 2.0 lifts acceptance. Deliberately strong - the
# literature finds surge is valued noticeably higher than the fare itself.
SURGE_WEIGHT = 0.50

# Of the drivers who do not accept, this share never answered at all rather
# than actively declining. Kept apart because they are different signals: a
# decline is a judgement about the ride, a timeout is a phone face-down.
EXPIRY_SHARE = 0.30

# Floor and ceiling, so no offer is ever certain either way. A driver who
# always accepts is not a driver, and a chain where every offer is hopeless
# would never terminate usefully.
MIN_ACCEPT_CHANCE = 0.05
MAX_ACCEPT_CHANCE = 0.95


@dataclass(frozen=True)
class Offer:
    """One offer and its outcome, ready to store and announce."""

    driver_id: str
    sequence: int
    status: str
    eta_seconds: int | None
    distance_to_pickup_m: int | None
    surge_multiplier: float
    offered_at: datetime
    expires_at: datetime
    responded_at: datetime | None

    @property
    def response_s(self) -> int | None:
        if self.responded_at is None:
            return None
        return int((self.responded_at - self.offered_at).total_seconds())


def accept_chance(eta_seconds: int | None, surge_multiplier: float) -> float:
    """How likely this driver is to take this ride.

    A missing ETA is treated as the reference distance rather than as no
    penalty: not knowing how far away the driver is should not make the
    offer look attractive.
    """
    eta = ETA_REFERENCE_S if eta_seconds is None else float(eta_seconds)
    # Saturating rather than linear: the difference between four and eight
    # minutes matters far more than between twenty and twenty-four.
    penalty = ETA_WEIGHT * (1.0 - math.exp(-eta / ETA_REFERENCE_S))
    bonus = 1.0 + (max(surge_multiplier, 1.0) - 1.0) * SURGE_WEIGHT
    chance = BASE_ACCEPT_CHANCE * (1.0 - penalty) * bonus
    return min(max(chance, MIN_ACCEPT_CHANCE), MAX_ACCEPT_CHANCE)


def run_chain(
    candidates: list[tuple[str, int | None, int | None]],
    surge_multiplier: float,
    now: datetime,
    rng: random.Random,
) -> tuple[str | None, list[Offer]]:
    """Offer the trip down the candidate list until somebody accepts.

    `candidates` is (driver_id, eta_seconds, distance_to_pickup_m) in the
    order dispatch wants to ask - nearest first. Returns the driver who
    accepted (or None if the chain was exhausted) and every offer made,
    including the refusals, which are the point.
    """
    outcomes: list[tuple[str, str, int | None, int | None, int | None]] = []
    winner: str | None = None

    for sequence, (driver_id, eta_seconds, distance_m) in enumerate(candidates, start=1):
        if rng.random() < accept_chance(eta_seconds, surge_multiplier):
            # Answering is quick when the answer is yes.
            outcomes.append((driver_id, "accepted", eta_seconds, distance_m,
                             rng.randint(1, 8)))
            winner = driver_id
            break
        if rng.random() < EXPIRY_SHARE:
            # Nobody answered: the offer runs the full deadline and the next
            # driver is not asked until it lapses. This is what makes an
            # undersupplied zone slow as well as unlucky.
            outcomes.append((driver_id, "expired", eta_seconds, distance_m, None))
        else:
            outcomes.append((driver_id, "declined", eta_seconds, distance_m,
                             rng.randint(2, OFFER_TIMEOUT_S - 1)))

    # Lay the chain out on the clock, ending at `now`. Running backwards
    # keeps matched_at where it already was and guarantees no milestone
    # lands in the future - see the module docstring.
    spans = [OFFER_TIMEOUT_S if response is None else response
             for _, _, _, _, response in outcomes]
    start = now - timedelta(seconds=sum(spans))

    offers: list[Offer] = []
    cursor = start
    for sequence, ((driver_id, status, eta_seconds, distance_m, response), span) in enumerate(
        zip(outcomes, spans), start=1
    ):
        offers.append(
            Offer(
                driver_id=driver_id,
                sequence=sequence,
                status=status,
                eta_seconds=eta_seconds,
                distance_to_pickup_m=distance_m,
                surge_multiplier=surge_multiplier,
                offered_at=cursor,
                expires_at=cursor + timedelta(seconds=OFFER_TIMEOUT_S),
                responded_at=None if response is None else cursor + timedelta(seconds=response),
            )
        )
        cursor += timedelta(seconds=span)

    return winner, offers
