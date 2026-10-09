"""An unmatched trip ends when _no_driver runs, not at the tick start."""

from datetime import datetime, timedelta, timezone

from dispatch_service import __main__ as dispatch


def test_no_driver_end_is_not_before_the_request(monkeypatch):
    tick = datetime(2026, 10, 8, 9, 0, 2, tzinfo=timezone.utc)
    requested = tick + timedelta(seconds=30)
    when_closed = requested + timedelta(seconds=1)

    monkeypatch.setattr(dispatch, "utc_now", lambda: when_closed)

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

    sent = []

    class Producer:
        def send(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(dispatch.postgres, "write_connection", lambda: Conn())

    dispatch._no_driver(
        Producer(),
        Producer(),
        {
            "rider_id": "psg-0000001",
            "dropoff_zone_id": "2",
            "passenger_count": 1,
            "requested_vehicle_type": "economy",
            "requested_at": requested,
        },
        "trp-20261008-nodriver1",
        "1",
        tick,
        [],
        1.0,
    )

    assert len(sent) == 1
    value = sent[0]["value"]
    assert value["status"] == "no_driver_found"
    assert value["ended_at"] == int(when_closed.timestamp() * 1000)
    assert value["event_time"] == value["ended_at"]
    assert value["ended_at"] > int(requested.timestamp() * 1000)
    assert value["ended_at"] > int(tick.timestamp() * 1000)
