"""A replica cancel on the snap inside route() stays inside that lookup."""

import psycopg

from nus_common import routing


def test_snap_serialization_failure_does_not_escape_route(monkeypatch):
    monkeypatch.setattr(routing, "ROUTE_RETRY_DELAY_S", 0)

    def fail():
        raise psycopg.errors.SerializationFailure(
            "canceling statement due to conflict with recovery"
        )

    class Broken:
        def __enter__(self):
            fail()
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(routing.postgres, "read_connection", lambda: Broken())

    assert routing.route(40.75, -73.99, 40.751, -73.989, "morning") is None
