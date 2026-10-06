"""matched_at is the clock after this trip's route, not the tick start."""

from datetime import datetime, timedelta, timezone
import random

from dispatch_service import __main__ as dispatch
from nus_common.offers import Offer


def test_matched_at_is_after_the_route_and_not_before_the_request(monkeypatch):
    tick = datetime(2026, 10, 5, 20, 51, 41, tzinfo=timezone.utc)
    requested = tick + timedelta(seconds=4)
    after_route = requested + timedelta(seconds=1)
    clock = {"now": tick}

    def utc_now():
        return clock["now"]

    def route(*_args, **_kwargs):
        clock["now"] = after_route
        return (1.2, 90, "LINESTRING(-73.99 40.75, -73.988 40.751)")

    offers = [
        Offer(
            driver_id="drv-0000001",
            sequence=1,
            status="accepted",
            eta_seconds=60,
            distance_to_pickup_m=400,
            surge_multiplier=1.0,
            offered_at=tick,
            expires_at=tick + timedelta(seconds=20),
            responded_at=tick + timedelta(seconds=5),
        )
    ]

    monkeypatch.setattr(dispatch, "utc_now", utc_now)
    monkeypatch.setattr(dispatch.routing, "route", route)
    monkeypatch.setattr(dispatch.pricing, "surge_for", lambda *_a, **_k: 1.0)
    monkeypatch.setattr(
        dispatch, "find_candidates",
        lambda *_a, **_k: [("drv-0000001", -73.99, 40.75, 60, 400)],
    )
    monkeypatch.setattr(dispatch, "run_chain", lambda *_a, **_k: ("drv-0000001", offers))
    monkeypatch.setattr(dispatch, "accept_already_stored", lambda _trip_id: False)
    monkeypatch.setattr(dispatch, "record_offers", lambda *_a, **_k: True)
    monkeypatch.setattr(dispatch, "announce_offers", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatch, "announce", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatch, "store_live_state", lambda *_a, **_k: None)

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, *_args, **_kwargs):
            return None

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

    class Redis:
        def zrem(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(dispatch.postgres, "write_connection", lambda: Conn())

    trip = dispatch.assign(
        {
            "trip_id": "trp-20261005-clock001",
            "rider_id": "psg-0000001",
            "pickup_lat": 40.75,
            "pickup_lon": -73.99,
            "dropoff_lat": 40.751,
            "dropoff_lon": -73.988,
            "pickup_zone_id": "1",
            "dropoff_zone_id": "1",
            "requested_vehicle_type": "economy",
            "passenger_count": 1,
            "requested_at": requested,
        },
        Redis(), None, Redis(), None, tick, "evening", random.Random(1), None,
        2.0, 3.0, 1.0, 0.5, 600, None, 5, 25.0,
    )

    assert trip is not None
    assert trip.matched_at == after_route
    assert trip.matched_at >= trip.requested_at
    assert trip.matched_at > tick
