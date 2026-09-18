"""Direct tests of Driver.move: the car walks the polyline, not the chord."""

import random

from nus_common.geo import is_chord_path, linestring_vertices

from driver_service.fleet import IDLE, Driver


L_WKT = "LINESTRING(-73.99 40.75, -73.99 40.76, -73.98 40.76)"
CHORD_WKT = "LINESTRING(-73.99 40.75, -73.98 40.76)"


def _on_l(lat: float, lon: float, tol: float = 2e-5) -> bool:
    on_north_leg = abs(lon - (-73.99)) <= tol and 40.75 - tol <= lat <= 40.76 + tol
    on_east_leg = abs(lat - 40.76) <= tol and -73.99 - tol <= lon <= -73.98 + tol
    return on_north_leg or on_east_leg


def _walk(path, ticks=80, speed_kmh=50.0, seconds=3.0):
    start = path[0]
    driver = Driver("drv-0000001", start[0], start[1], "1")
    driver.set_status(IDLE)
    driver.follow(path)
    rng = random.Random(0)
    trail = [(driver.lat, driver.lon)]
    for _ in range(ticks):
        driver.move(seconds, speed_kmh, rng)
        trail.append((driver.lat, driver.lon))
        if driver.arrived():
            break
    return driver, trail


def test_walk_along_l_stays_on_the_street():
    path = linestring_vertices(L_WKT)
    driver, trail = _walk(path)
    off = [p for p in trail if not _on_l(*p)]
    assert off == [], f"tail left the street: {off[:3]}"
    assert driver.arrived()
    assert abs(driver.lat - 40.76) < 1e-6
    assert abs(driver.lon - (-73.98)) < 1e-6


def test_two_point_chord_is_the_failing_shape():
    chord = linestring_vertices(CHORD_WKT)
    assert is_chord_path(chord)
    _, trail = _walk(chord)
    # Walking the chord puts the car on the hypotenuse, which is exactly
    # the "cut the block / fly over water" failure the dashboards show.
    mid = trail[len(trail) // 2]
    assert not _on_l(*mid, tol=1e-4)


def test_follow_empty_path_does_not_move():
    driver = Driver("drv-0000002", 40.75, -73.99, "1")
    driver.set_status(IDLE)
    driver.follow([])
    driver.move(3.0, 40.0, random.Random(1))
    assert driver.lat == 40.75
    assert driver.lon == -73.99


def test_become_idle_clears_the_polyline():
    path = linestring_vertices(L_WKT)
    driver = Driver("drv-0000003", path[0][0], path[0][1], "1")
    driver.set_status(IDLE)
    driver.follow(path)
    driver.become_idle()
    assert driver.path == []
    assert driver.status == IDLE
    assert driver.trip_id is None
    assert driver.arrived()
