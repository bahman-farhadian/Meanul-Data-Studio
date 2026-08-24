"""The life of a trip, from matched to finished.

Dispatch owns the status machine. It is the only service that decides a trip
has moved on, which means there is exactly one place to look when a trip is
stuck, and no two services can ever disagree about what state a trip is in.

    requested -> matched -> accepted -> en_route_pickup -> in_progress -> completed

and, at the points where they are possible, the three ways a trip ends early:
cancelled_by_driver, cancelled_by_passenger, and no_driver_found (which
happens before any of this, when nobody could be given the trip at all).

Nothing here talks to Kafka, Redis or the database. This file is about when
a trip changes state; __main__.py is about telling everyone that it did.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

# What comes after what, and how long the step normally takes in seconds.
# The pickup drive and the trip itself are worked out per trip, so they are
# not in this table.
STEP_SECONDS = {
    "matched": (3, 12),      # the driver's phone rings, the driver taps accept
    "accepted": (1, 5),      # the car pulls away
}


@dataclass
class ActiveTrip:
    trip_id: str
    rider_id: str
    driver_id: str
    pickup_lat: float
    pickup_lon: float
    dropoff_lat: float
    dropoff_lon: float
    pickup_zone_id: str

    route_km: float
    predicted_duration_s: int
    surge_multiplier: float
    fare_estimate: float

    status: str
    # When this trip should move on to whatever comes next.
    next_change_at: datetime
    # Set when the car actually starts moving with the rider in it.
    started_at: datetime | None = None

    def progress(self, now: datetime) -> float:
        """How far along the journey the car is, from 0.0 to 1.0.

        Used to say where the rider's phone is. It assumes a steady speed,
        which is not true of any real car, but is close enough for a position
        report and costs nothing to compute.
        """
        if self.started_at is None or self.predicted_duration_s <= 0:
            return 0.0
        elapsed = (now - self.started_at).total_seconds()
        return max(0.0, min(elapsed / self.predicted_duration_s, 1.0))

    def current_position(self, now: datetime) -> tuple[float, float]:
        """Where the car is, somewhere between the two ends."""
        share = self.progress(now)
        return (
            self.pickup_lat + (self.dropoff_lat - self.pickup_lat) * share,
            self.pickup_lon + (self.dropoff_lon - self.pickup_lon) * share,
        )


def next_status(
    trip: ActiveTrip,
    now: datetime,
    rng: random.Random,
    pickup_drive_seconds: int,
    cancel_by_driver_chance: float,
    cancel_by_passenger_chance: float,
) -> tuple[str, datetime] | None:
    """Work out what happens to this trip next, or None if not yet.

    Returns the new status and when the one after that is due.
    """
    if now < trip.next_change_at:
        return None

    if trip.status == "matched":
        # The driver can still say no at this point.
        if rng.random() < cancel_by_driver_chance:
            return "cancelled_by_driver", now
        low, high = STEP_SECONDS["accepted"]
        return "accepted", now + timedelta(seconds=rng.randint(low, high))

    if trip.status == "accepted":
        # The car sets off to collect the rider.
        return "en_route_pickup", now + timedelta(seconds=pickup_drive_seconds)

    if trip.status == "en_route_pickup":
        # Waiting is where riders give up, so this is where that can happen.
        if rng.random() < cancel_by_passenger_chance:
            return "cancelled_by_passenger", now
        # The journey itself takes as long as the route said, give or take.
        real_duration = int(trip.predicted_duration_s * rng.triangular(0.75, 1.6, 1.05))
        return "in_progress", now + timedelta(seconds=max(real_duration, 60))

    if trip.status == "in_progress":
        return "completed", now

    return None


def first_change_at(now: datetime, rng: random.Random) -> datetime:
    """When a freshly matched trip should first move on."""
    low, high = STEP_SECONDS["matched"]
    return now + timedelta(seconds=rng.randint(low, high))
