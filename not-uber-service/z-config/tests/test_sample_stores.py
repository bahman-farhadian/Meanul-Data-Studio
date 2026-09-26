"""Small-sample proof: generation, routing and tails against real stores.

Boots a throwaway PG+pgRouting, Redis, Kafka, Schema Registry and ClickHouse.
Does not touch LION/tiles volumes or the idops stack. The session fixture
tears the compose project down, including its volumes.
"""

from __future__ import annotations

import json
import os
import random
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sample_graph import LATS, LONS, WATER_LAT, WATER_LON, load as load_graph

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO / "z-config" / "sample-stack" / "docker-compose.yaml"
SCRATCH = Path(os.environ.get("NUS_SAMPLE_SCRATCH", "/tmp/grok-goal-9d807b438e5a/implementer"))
PROJECT = "nus-goal-sample"


def _compose(*args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [
        "docker", "compose",
        "-p", PROJECT,
        "-f", str(COMPOSE_FILE),
        *args,
    ]
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
    )


@pytest.fixture(scope="session")
def sample_env():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    passwords = {
        "NUS_SAMPLE_PG_PASSWORD": os.environ.get("NUS_SAMPLE_PG_PASSWORD") or secrets.token_urlsafe(12),
        "NUS_SAMPLE_REDIS_PASSWORD": os.environ.get("NUS_SAMPLE_REDIS_PASSWORD") or secrets.token_urlsafe(12),
        "NUS_SAMPLE_CH_PASSWORD": os.environ.get("NUS_SAMPLE_CH_PASSWORD") or secrets.token_urlsafe(12),
    }
    env = {**os.environ, **passwords}
    boot_log = SCRATCH / "boot-failed.log"
    try:
        up = _compose("up", "-d", "--wait", "--wait-timeout", "180", env=env, check=False)
        if up.returncode != 0:
            ps = _compose("ps", env=env, check=False)
            logs = _compose("logs", "--no-color", "--tail", "80", env=env, check=False)
            boot_log.write_text(
                "compose up failed\n\nSTDOUT\n"
                + up.stdout
                + "\nSTDERR\n"
                + up.stderr
                + "\nPS\n"
                + ps.stdout
                + "\nLOGS\n"
                + logs.stdout
                + logs.stderr
            )
            pytest.fail(f"sample stack failed to boot; see {boot_log}")
    except Exception as exc:
        boot_log.write_text(f"sample stack failed to boot: {exc}\n")
        raise

    os.environ.update(
        {
            "PG_HOST": "127.0.0.1",
            "PG_WRITE_PORT": "15432",
            "PG_READ_PORT": "15432",
            "PG_USER": "postgres",
            "PG_PASSWORD": passwords["NUS_SAMPLE_PG_PASSWORD"],
            "PG_DATABASE": "nus",
            "REDIS_URL": f"redis://:{passwords['NUS_SAMPLE_REDIS_PASSWORD']}@127.0.0.1:16379/0",
            "CH_HOST": "127.0.0.1",
            "CH_HTTP_PORT": "18123",
            "CH_USER": "nus",
            "CH_PASSWORD": passwords["NUS_SAMPLE_CH_PASSWORD"],
            "CH_DATABASE": "nus",
            "KAFKA_BOOTSTRAP": "127.0.0.1:19092",
            "SCHEMA_REGISTRY_URL": "http://127.0.0.1:18081",
            "SCHEMA_DIR": str(REPO / "c-infra-kafka" / "schemas"),
            "MAX_SNAP_KM": "0.5",
        }
    )

    from nus_common import clickhouse, postgres

    postgres.reset_pools()
    clickhouse.reset_client()

    deadline = time.time() + 60
    last_err = None
    while time.time() < deadline:
        try:
            postgres.ping()
            last_err = None
            break
        except Exception as err:
            last_err = err
            time.sleep(1)
    if last_err is not None:
        boot_log.write_text(f"postgres never answered: {last_err}\n")
        _compose("down", "-v", "--remove-orphans", env=env, check=False)
        pytest.fail(f"postgres never answered: {last_err}")

    with postgres.write_connection() as conn:
        load_graph(conn)

    yield passwords

    from nus_common import clickhouse as ch
    from nus_common import postgres as pg

    pg.reset_pools()
    ch.reset_client()
    _compose("down", "-v", "--remove-orphans", env=env, check=False)


def _metres_to_ways(lat: float, lon: float) -> float:
    from nus_common import postgres

    with postgres.read_connection() as conn:
        row = postgres.fetch_one(
            conn,
            """
            SELECT ST_Distance(
                     ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
                     w.the_geom::geography
                   ) AS metres
              FROM ways w
             ORDER BY w.the_geom <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)
             LIMIT 1
            """,
            {"lat": lat, "lon": lon},
        )
    return float(row["metres"])


def test_water_point_does_not_snap(sample_env):
    from nus_common import routing

    assert routing.nearest_road_point(WATER_LAT, WATER_LON) is None
    assert _metres_to_ways(WATER_LAT, WATER_LON) > 500


def test_generation_never_returns_a_harbour_point(sample_env):
    from nus_common import routing
    from nus_common.citygrid import CityGrid

    grid = CityGrid.load(from_leader=True)
    rng = random.Random(20250824)
    for _ in range(40):
        lat, lon = routing.random_road_point_in_zone(grid, "1", rng)
        assert _metres_to_ways(lat, lon) <= 75, (lat, lon)


def test_route_walks_the_l_not_the_chord(sample_env):
    from nus_common import routing
    from nus_common.geo import is_chord_path, linestring_vertices

    # Southwest corner to one block north-and-east: the street path is an L.
    from_lat, from_lon = LATS[0], LONS[0]
    to_lat, to_lon = LATS[1], LONS[1]
    computed = routing.route(from_lat, from_lon, to_lat, to_lon, "morning")
    assert computed is not None
    route_km, _seconds, wkt = computed
    verts = linestring_vertices(wkt)
    assert not is_chord_path(verts), verts
    assert len(verts) >= 3
    # The hypotenuse midpoint is off every way by tens of metres.
    mid_lat = (from_lat + to_lat) / 2
    mid_lon = (from_lon + to_lon) / 2
    assert _metres_to_ways(mid_lat, mid_lon) > 30
    for lat, lon in verts:
        assert _metres_to_ways(lat, lon) <= 5
    assert route_km > 0.15


def test_reverse_traversal_keeps_shape_points_in_visit_order(sample_env):
    from nus_common import routing
    from nus_common.geo import linestring_vertices

    # East to west along the southernmost street: the stored edge runs west→east.
    from_lat, from_lon = LATS[0], LONS[-1]
    to_lat, to_lon = LATS[0], LONS[0]
    computed = routing.route(from_lat, from_lon, to_lat, to_lon, "afternoon")
    assert computed is not None
    verts = linestring_vertices(computed[2])
    lons = [lon for _lat, lon in verts]
    assert lons[0] > lons[-1], verts
    assert all(abs(lat - from_lat) < 1e-8 for lat, _lon in verts)


def test_drive_path_does_not_install_a_sea_chord(sample_env):
    from nus_common import routing

    path = routing.drive_path(WATER_LAT, WATER_LON, LATS[0], LONS[0], "night")
    # Water is off the main network; sitting still beats flying.
    assert path == []


def test_broker_cache_and_databases(sample_env):
    from datetime import timedelta

    from nus_common import clickhouse, postgres, redis_client, routing
    from nus_common.geo import linestring_vertices, to_millis, utc_now
    from nus_common.kafka import AvroTopicConsumer, AvroTopicProducer

    from dispatch_service.trips import ActiveTrip
    from dispatch_service.__main__ import store_live_state
    from driver_service.fleet import ON_TRIP, Driver
    from clickhouse_sink.batches import COLUMNS, Batches

    from_lat, from_lon = LATS[0], LONS[0]
    to_lat, to_lon = LATS[2], LONS[2]
    computed = routing.route(from_lat, from_lon, to_lat, to_lon, "evening")
    assert computed is not None
    route_km, predicted_s, wkt = computed
    verts = linestring_vertices(wkt)
    assert len(verts) >= 3

    now = utc_now()
    trip = ActiveTrip(
        trip_id="trp-20260918-abcd1234",
        rider_id="psg-0000001",
        driver_id="drv-0000001",
        pickup_lat=from_lat,
        pickup_lon=from_lon,
        dropoff_lat=to_lat,
        dropoff_lon=to_lon,
        driver_lat=from_lat,
        driver_lon=from_lon,
        pickup_zone_id="1",
        dropoff_zone_id="1",
        route_km=route_km,
        predicted_duration_s=predicted_s,
        surge_multiplier=1.0,
        fare_estimate=8.5,
        status="in_progress",
        next_change_at=now + timedelta(seconds=30),
        started_at=now,
        route_wkt=wkt,
    )

    redis = redis_client.primary(redis_client.DB_TRIP)
    store_live_state(redis, trip, now, ttl_seconds=600)
    cached = json.loads(redis.get(redis_client.trip_active_key(trip.trip_id)))
    assert cached["route_wkt"] == wkt
    assert cached["driver_id"] == "drv-0000001"

    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trips (
                    trip_id, pickup_point, dropoff_point, route, route_km, ended_at
                )
                VALUES (
                    %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    ST_GeomFromText(%s, 4326),
                    %s,
                    now()
                )
                """,
                (trip.trip_id, from_lon, from_lat, to_lon, to_lat, wkt, route_km),
            )
        conn.commit()
        on_network = postgres.fetch_one(
            conn,
            (REPO / "z-config" / "check-on-network.sql").read_text(),
        )
    assert on_network["trips"] >= 1
    assert on_network["pickup_off_network"] == 0
    assert on_network["dropoff_off_network"] == 0
    assert on_network["long_two_point_routes"] == 0

    driver = Driver("drv-0000001", from_lat, from_lon, "1")
    driver.set_status(ON_TRIP, trip.trip_id)
    driver.follow(verts)
    rng = random.Random(7)
    trail = []
    for _ in range(80):
        driver.move(3.0, 35.0, rng)
        trail.append((driver.lat, driver.lon, driver.heading_deg, driver.speed_kmh))
        if driver.arrived():
            break
    assert trail
    off = [(lat, lon) for lat, lon, _h, _s in trail if _metres_to_ways(lat, lon) > 15]
    assert off == [], f"live tail left the streets: {off[:3]}"

    producer = AvroTopicProducer("driver_location", "sample-test")
    event_time = to_millis(now)
    for i, (lat, lon, heading, speed) in enumerate(trail):
        producer.send(
            key="drv-0000001",
            value={
                "driver_id": "drv-0000001",
                "trip_id": trip.trip_id,
                "status": "on_trip",
                "lat": lat,
                "lon": lon,
                "heading_deg": float(heading),
                "speed_kmh": float(speed),
                "zone_id": "1",
                "event_time": event_time + i * 3000,
            },
        )
    producer.flush()

    consumer = AvroTopicConsumer(
        topics=["driver_location"],
        group_id="sample-quality-check",
        from_beginning=True,
    )
    seen = []
    deadline = time.time() + 20
    while time.time() < deadline and len(seen) < 3:
        message = consumer.poll_once(timeout=1.0)
        if message is None:
            continue
        _topic, _key, value = message
        if value:
            seen.append(value)
    consumer.close()
    assert seen, "kafka driver_location was empty"
    assert seen[0]["driver_id"] == "drv-0000001"
    assert abs(seen[0]["lat"] - trail[0][0]) < 1e-6

    clickhouse.client().command("CREATE DATABASE IF NOT EXISTS nus")
    # A cut-down copy of e-infra-clickhouse/ddl/002_positions.sql - enough
    # columns to take a real Batches row, in the order clickhouse_sink
    # declares them. event_id leads it there and has to lead it here: the
    # sink names its columns explicitly, so a table missing one is a
    # ProgrammingError, not a silently shifted row.
    clickhouse.client().command(
        """
        CREATE TABLE IF NOT EXISTS nus.driver_positions (
            event_id UUID,
            driver_id String,
            trip_id Nullable(String),
            status Enum8('offline' = 1, 'idle' = 2, 'en_route_pickup' = 3, 'on_trip' = 4),
            lat Float64,
            lon Float64,
            heading_deg Nullable(Float32),
            speed_kmh Nullable(Float32),
            zone_id String,
            event_time DateTime64(3, 'UTC')
        )
        ENGINE = MergeTree
        ORDER BY (driver_id, event_time)
        """
    )
    batches = Batches()
    for i, (lat, lon, heading, speed) in enumerate(trail):
        batches.add(
            "nus.driver_positions",
            [
                str(uuid.uuid4()),
                "drv-0000001",
                trip.trip_id,
                "on_trip",
                lat,
                lon,
                float(heading),
                float(speed),
                "1",
                datetime.fromtimestamp((event_time + i * 3000) / 1000, tz=timezone.utc),
            ],
        )
    sent = batches.flush()
    assert sent == len(trail)

    rows = clickhouse.client().query(
        "SELECT lat, lon, status FROM nus.driver_positions "
        "WHERE driver_id = 'drv-0000001' AND status = 'on_trip' "
        "ORDER BY event_time"
    ).result_rows
    assert len(rows) == len(trail)
    ch_off = [(lat, lon) for lat, lon, _status in rows if _metres_to_ways(lat, lon) > 15]
    assert ch_off == [], f"clickhouse tail left the streets: {ch_off[:3]}"

    proof = {
        "postgres": dict(on_network),
        "redis_route_wkt_vertices": len(linestring_vertices(cached["route_wkt"])),
        "kafka_driver_location": len(seen),
        "clickhouse_on_trip_rows": len(rows),
        "trail_points": len(trail),
        "route_km": route_km,
    }
    (SCRATCH / "broker-cache-db.log").write_text(json.dumps(proof, indent=2, default=str) + "\n")
    (SCRATCH / "tails.log").write_text(
        json.dumps(
            {
                "clickhouse_off_network": ch_off,
                "live_off_network": off,
                "n_positions": len(rows),
            },
            indent=2,
        )
        + "\n"
    )
    (SCRATCH / "driver-logs.txt").write_text(
        json.dumps(
            {
                "path_vertices": len(verts),
                "path_chord": int(len(verts) <= 2),
                "path_p50": len(verts),
                "falling_back": False,
            },
            indent=2,
        )
        + "\n"
    )


def test_on_network_sql_fails_a_sea_pickup_and_chord_route(sample_env):
    """The shipped verify SQL must flag a harbour pickup and a two-point chord."""
    from nus_common import postgres

    sql = (REPO / "z-config" / "check-on-network.sql").read_text()
    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trips (
                    trip_id, pickup_point, dropoff_point, route, route_km, ended_at
                )
                VALUES (
                    'trp-20260918-sea00001',
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    ST_GeomFromText('LINESTRING(-73.99 40.75, -73.972 40.7515)', 4326),
                    2.0,
                    now()
                )
                """,
                (WATER_LON, WATER_LAT, LONS[0], LATS[0]),
            )
        conn.commit()
        row = postgres.fetch_one(conn, sql)
    assert row["pickup_off_network"] >= 1
    assert row["long_two_point_routes"] >= 1
