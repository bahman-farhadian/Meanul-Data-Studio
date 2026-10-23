"""A replica cancel on the snap inside route() stays inside that lookup."""

import psycopg

from hailpath_common import routing


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


def test_drive_path_to_the_same_point_asks_postgres_nothing(monkeypatch):
    """The coordinates Dionysus logged on three ticks running.

    A driver whose new target snapped onto the vertex under its own wheels
    used to spend a pgr_ksp call to learn there is no path from a node to
    itself, and then logged it as a missing street path on the driver
    tick's hot path.
    """
    calls = []
    monkeypatch.setattr(routing, "route", lambda *a, **k: calls.append(a) or None)
    warnings = []
    monkeypatch.setattr(routing.log, "warning", lambda *a, **k: warnings.append(a))

    lat, lon = 40.83898208951047, -73.91859196134591
    assert routing.drive_path(lat, lon, lat, lon, "morning") == []
    assert calls == [], "drive_path still routed a driver to where it already is"
    assert warnings == [], "a point that needs no path is not a missing street path"


def test_drive_path_still_warns_when_a_real_pair_has_no_path(monkeypatch):
    """The guard must not swallow the case it was carved out of."""
    monkeypatch.setattr(routing, "route", lambda *a, **k: None)
    warnings = []
    monkeypatch.setattr(routing.log, "warning", lambda *a, **k: warnings.append(a))

    assert routing.drive_path(40.75, -73.99, 40.751, -73.989, "morning") == []
    assert len(warnings) == 1
