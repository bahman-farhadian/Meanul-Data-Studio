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


# --- the bounded edge list -------------------------------------------------
#
# pgRouting reads every row its edge SQL returns into C++ memory before the
# search starts, so an unbounded list made every call read all 172k ways.
# The box is what stops that; these pin down that it stays a box the real
# route fits inside, and that nothing is ever called unroutable on the
# strength of the box alone.

JFK = (40.6413, -73.7781)
TIMES_SQ = (40.7580, -73.9855)


def test_envelope_contains_both_points_with_the_floor_padding():
    lat_a, lon_a = TIMES_SQ
    lat_b, lon_b = 40.7590, -73.9840  # ~180m away, well under the floor
    min_lon, min_lat, max_lon, max_lat = routing._route_envelope(
        lat_a, lon_a, lat_b, lon_b, min_pad_km=3.0, pad_factor=0.6
    )

    assert min_lat < min(lat_a, lat_b) and max_lat > max(lat_a, lat_b)
    assert min_lon < min(lon_a, lon_b) and max_lon > max(lon_a, lon_b)

    # The floor, not the proportional part, has to be what applied here.
    from hailpath_common.geo import distance_km

    south_pad = distance_km(min_lat, lon_a, min(lat_a, lat_b), lon_a)
    assert 2.9 < south_pad < 3.1, south_pad


def test_envelope_padding_grows_with_the_trip():
    short = routing._route_envelope(*TIMES_SQ, 40.7590, -73.9840, 3.0, 0.6)
    long_ = routing._route_envelope(*TIMES_SQ, *JFK, 3.0, 0.6)

    short_height = short[3] - short[1]
    long_height = long_[3] - long_[1]
    assert long_height > short_height * 2, (short_height, long_height)


def test_envelope_is_wider_in_longitude_than_latitude_degrees():
    """A degree of longitude is ~0.76 of a degree of latitude at New York."""
    min_lon, min_lat, max_lon, max_lat = routing._route_envelope(
        *TIMES_SQ, 40.7590, -73.9840, 3.0, 0.6
    )
    lat_pad = (max_lat - min_lat) - abs(40.7590 - TIMES_SQ[0])
    lon_pad = (max_lon - min_lon) - abs(-73.9840 - TIMES_SQ[1])
    assert lon_pad > lat_pad * 1.2, (lat_pad, lon_pad)


def test_envelope_clears_the_snap_radius():
    """An endpoint snaps up to MAX_SNAP_KM away; the box must still hold it."""
    min_lon, min_lat, max_lon, max_lat = routing._route_envelope(
        *TIMES_SQ, *TIMES_SQ, 3.0, 0.6
    )
    from hailpath_common.geo import distance_km

    assert distance_km(min_lat, TIMES_SQ[1], *TIMES_SQ) > 0.5
    assert distance_km(TIMES_SQ[0], min_lon, *TIMES_SQ) > 0.5


def test_edge_queries_tries_the_box_then_the_whole_city(monkeypatch):
    monkeypatch.delenv("ROUTE_BBOX_PAD_FACTOR", raising=False)
    monkeypatch.delenv("ROUTE_BBOX_MIN_PAD_KM", raising=False)
    queries = routing._edge_queries("morning", *TIMES_SQ, *JFK)

    assert len(queries) == 2
    assert "ST_MakeEnvelope" in queries[0]
    assert "ST_MakeEnvelope" not in queries[1]
    assert all("st.period = 'morning'" in q for q in queries)


def test_bounding_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("ROUTE_BBOX_PAD_FACTOR", "0")
    queries = routing._edge_queries("night", *TIMES_SQ, *JFK)

    assert len(queries) == 1
    assert "ST_MakeEnvelope" not in queries[0]


def _ksp_rows():
    return [{"path_id": 1, "path_seq": 1, "node": 10, "edge": 5, "cost": 12.0},
            {"path_id": 1, "path_seq": 2, "node": 11, "edge": -1, "cost": 0.0}]


def _patch_route_plumbing(monkeypatch, ksp_results):
    """Make route() run without Postgres, recording each edge SQL it tries."""
    monkeypatch.setattr(routing, "nearest_road_point", lambda lat, lon: (lat, lon))
    tried = []

    def fake_query(fetch, sql, params, what, **extra):
        if what == "pgr_ksp call":
            tried.append(params["edges_sql"])
            return ksp_results.pop(0)
        return {"route_km": 1.5, "route_wkt": "LINESTRING(0 0,1 1)"}

    monkeypatch.setattr(routing, "_query_with_retry", fake_query)
    return tried


def test_route_falls_back_to_the_whole_city_when_the_box_is_empty(monkeypatch):
    tried = _patch_route_plumbing(monkeypatch, [[], _ksp_rows()])

    assert routing.route(*TIMES_SQ, *JFK, "morning") is not None
    assert len(tried) == 2, "the unbounded retry did not run"
    assert "ST_MakeEnvelope" in tried[0]
    assert "ST_MakeEnvelope" not in tried[1]


def test_route_does_not_scan_the_city_when_the_box_answers(monkeypatch):
    tried = _patch_route_plumbing(monkeypatch, [_ksp_rows()])

    assert routing.route(*TIMES_SQ, *JFK, "morning") is not None
    assert len(tried) == 1, "the whole-city query ran even though the box answered"
    assert "ST_MakeEnvelope" in tried[0]


def test_route_still_reports_no_path_when_neither_query_finds_one(monkeypatch):
    tried = _patch_route_plumbing(monkeypatch, [[], []])

    assert routing.route(*TIMES_SQ, *JFK, "morning") is None
    assert len(tried) == 2, "a pair was called unroutable on the box alone"
