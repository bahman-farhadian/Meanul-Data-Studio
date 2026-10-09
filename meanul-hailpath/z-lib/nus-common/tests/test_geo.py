"""Direct tests of shipped geo helpers: street polylines vs a two-point chord."""

from nus_common.geo import (
    is_chord_path,
    linestring_vertices,
    point_at_fraction,
    points_along_linestring,
)


# L-shaped street: north along -73.99, then east along 40.76.
# WKT is lon lat.
L_WKT = "LINESTRING(-73.99 40.75, -73.99 40.76, -73.98 40.76)"
CHORD_WKT = "LINESTRING(-73.99 40.75, -73.98 40.76)"


def _on_l(lat: float, lon: float, tol: float = 1e-5) -> bool:
    """True when (lat, lon) sits on one of the two L legs, not the hypotenuse."""
    on_north_leg = abs(lon - (-73.99)) <= tol and 40.75 - tol <= lat <= 40.76 + tol
    on_east_leg = abs(lat - 40.76) <= tol and -73.99 - tol <= lon <= -73.98 + tol
    return on_north_leg or on_east_leg


def test_linestring_vertices_lonlat_to_latlon():
    verts = linestring_vertices(L_WKT)
    assert verts[0] == (40.75, -73.99)
    assert verts[-1] == (40.76, -73.98)
    assert len(verts) == 3


def test_points_along_l_stay_on_the_street():
    points = points_along_linestring(L_WKT, 17)
    assert len(points) == 17
    off = [p for p in points if not _on_l(*p)]
    assert off == [], f"street sample left the L: {off[:3]}"


def test_points_along_chord_cut_the_corner():
    points = points_along_linestring(CHORD_WKT, 9)
    # Midpoint of the chord is inside the L, not on either leg.
    mid = points[len(points) // 2]
    assert not _on_l(*mid, tol=1e-4)


def test_point_at_fraction_hits_the_corner():
    verts = linestring_vertices(L_WKT)
    # First leg and second leg are similar length in this fixture; the
    # corner is the only vertex that is on both legs.
    corner = point_at_fraction(verts, 0.5)
    assert _on_l(*corner)


def test_is_chord_path_rejects_long_two_point_hop():
    verts = linestring_vertices(CHORD_WKT)
    assert is_chord_path(verts)
    assert not is_chord_path(linestring_vertices(L_WKT))
    assert not is_chord_path([(40.75, -73.99), (40.7505, -73.99)])
    assert not is_chord_path([])
