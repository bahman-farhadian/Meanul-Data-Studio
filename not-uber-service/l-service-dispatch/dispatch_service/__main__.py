"""Match trips to drivers, route them, price them, and see them through.

Every tick:

  1. take whatever ride requests are waiting;
  2. for each one, find the nearest free driver, compute the real route, work
     out the price, and announce the match;
  3. move every trip already under way to its next status when it is due;
  4. keep the live trip state in Redis current, so the other services can see
     where each car is without asking the database.

This is the only service that decides a trip has changed status. One owner
means one place to look when a trip is stuck, and no chance of two services
disagreeing about what state a trip is in.
"""

import json
import random
import sys
import time
from datetime import datetime

from nus_common import config, postgres, redis_client
from nus_common.citygrid import CityGrid
from nus_common.geo import day_period, distance_km, to_millis, utc_now
from nus_common.kafka import AvroTopicConsumer, AvroTopicProducer
from nus_common.lifecycle import Shutdown, wait_for_bootstrap
from nus_common.logging import get_logger, setup_logging

from dispatch_service import pricing, routing
from dispatch_service.trips import ActiveTrip, first_change_at, next_status

log = get_logger(__name__)

REQUEST_TOPIC = "trip_requests"
LIFECYCLE_TOPIC = "trip_lifecycle"

FINISHED = {
    "completed", "cancelled_by_passenger", "cancelled_by_driver", "no_driver_found"
}

UPDATE_ASSIGNED = """
    UPDATE trips
       SET driver_id = %(driver_id)s,
           status = %(status)s,
           route = ST_GeomFromText(%(route_wkt)s, 4326),
           route_km = %(route_km)s,
           predicted_duration_s = %(predicted_duration_s)s,
           surge_multiplier = %(surge_multiplier)s,
           fare_estimate = %(fare_estimate)s,
           matched_at = now(),
           updated_at = now()
     WHERE trip_id = %(trip_id)s
"""

UPDATE_STATUS = """
    UPDATE trips
       SET status = %(status)s,
           updated_at = now(),
           accepted_at = CASE WHEN %(status)s = 'accepted' THEN now() ELSE accepted_at END,
           started_at  = CASE WHEN %(status)s = 'in_progress' THEN now() ELSE started_at END,
           ended_at    = CASE WHEN %(status)s IN ('completed', 'cancelled_by_passenger',
                                                  'cancelled_by_driver', 'no_driver_found')
                              THEN now() ELSE ended_at END,
           actual_duration_s = COALESCE(%(actual_duration_s)s, actual_duration_s),
           fare_final = COALESCE(%(fare_final)s, fare_final)
     WHERE trip_id = %(trip_id)s
"""


def find_driver(redis, lat: float, lon: float, radius_km: float) -> str | None:
    """The nearest free driver, or None if there is nobody close enough.

    Redis keeps the free drivers in a geo set that driver-service updates
    every few seconds, so this is one fast lookup rather than a query over
    the whole fleet. Nothing here touches PostgreSQL.
    """
    found = redis.geosearch(
        redis_client.GEO_AVAILABLE_DRIVERS,
        longitude=lon, latitude=lat,
        radius=radius_km, unit="km",
        sort="ASC", count=1,
    )
    return found[0] if found else None


def announce(producer: AvroTopicProducer, trip: ActiveTrip, status: str,
             now: datetime, actual_duration_s: int | None = None,
             fare_final: float | None = None) -> None:
    """Tell everyone that a trip has changed status."""
    producer.send(
        key=trip.trip_id,
        value={
            "trip_id": trip.trip_id,
            "rider_id": trip.rider_id,
            "driver_id": trip.driver_id,
            "status": status,
            "pickup_zone_id": trip.pickup_zone_id,
            "route_km": trip.route_km,
            "predicted_duration_s": trip.predicted_duration_s,
            "actual_duration_s": actual_duration_s,
            "surge_multiplier": trip.surge_multiplier,
            "fare_estimate": trip.fare_estimate,
            "fare_final": fare_final,
            "event_time": to_millis(now),
        },
    )


def store_live_state(redis, trip: ActiveTrip, now: datetime, ttl_seconds: int) -> None:
    """Write what the other services need to know about a running trip.

    This is the live state, not the database record: where the car is now,
    where it is going, and what the route said it would take. It carries a
    lifetime, so a trip that somehow never finishes cannot leave a key behind
    for ever.
    """
    lat, lon = trip.current_position(now)
    redis.set(
        redis_client.trip_active_key(trip.trip_id),
        json.dumps(
            {
                "trip_id": trip.trip_id,
                "rider_id": trip.rider_id,
                "driver_id": trip.driver_id,
                "status": trip.status,
                "pickup_lat": trip.pickup_lat, "pickup_lon": trip.pickup_lon,
                "dropoff_lat": trip.dropoff_lat, "dropoff_lon": trip.dropoff_lon,
                "current_lat": lat, "current_lon": lon,
                "pickup_zone_id": trip.pickup_zone_id,
                "route_km": trip.route_km,
                "predicted_duration_s": trip.predicted_duration_s,
                "surge_multiplier": trip.surge_multiplier,
                "fare_estimate": trip.fare_estimate,
            }
        ),
        ex=ttl_seconds,
    )


def main() -> int:
    setup_logging("dispatch-service")
    shutdown = Shutdown()
    rng = random.Random(config.integer("RANDOM_SEED", 20250824))
    grid = CityGrid.from_environment()

    tick_seconds = config.number("DISPATCH_TICK_SECONDS", 1.0)
    search_radius_km = config.number("DISPATCH_SEARCH_RADIUS_KM", 5.0)
    base_fare = config.number("FARE_BASE", 3.0)
    per_km = config.number("FARE_PER_KM", 1.75)
    per_minute = config.number("FARE_PER_MINUTE", 0.45)
    cancel_by_driver = config.number("CANCEL_BY_DRIVER_CHANCE", 0.06)
    cancel_by_passenger = config.number("CANCEL_BY_PASSENGER_CHANCE", 0.07)
    active_ttl = config.integer("TRIP_ACTIVE_TTL_SECONDS", 7200)
    max_per_tick = config.integer("DISPATCH_MAX_REQUESTS_PER_TICK", 50)

    redis = redis_client.primary()
    wait_for_bootstrap(redis, shutdown)

    producer = AvroTopicProducer(LIFECYCLE_TOPIC)
    consumer = AvroTopicConsumer(
        topics=[REQUEST_TOPIC],
        group_id=config.optional("KAFKA_GROUP_ID", "dispatch-service"),
        # From the beginning: a request nobody answered is a rider still
        # waiting, so a restart should pick those up rather than skip them.
        from_beginning=True,
    )

    active: dict[str, ActiveTrip] = {}
    matched = 0
    unmatched = 0
    completed = 0

    try:
        while not shutdown.requested:
            started = time.monotonic()
            now = utc_now()
            period = day_period(now)

            # --- 1 and 2. new requests ---------------------------------
            taken = 0
            while taken < max_per_tick:
                message = consumer.poll_once(timeout=0.0)
                if message is None:
                    break
                taken += 1
                _, _, request = message
                if not request:
                    continue

                trip = assign(
                    request, redis, producer, now, period, rng, grid,
                    search_radius_km, base_fare, per_km, per_minute,
                )
                if trip is None:
                    unmatched += 1
                    continue

                active[trip.trip_id] = trip
                store_live_state(redis, trip, now, active_ttl)
                matched += 1

            if taken:
                # Position is saved only after the requests have been dealt
                # with, so a crash repeats a match instead of dropping a rider.
                consumer.commit()

            # --- 3. move trips along -----------------------------------
            for trip in list(active.values()):
                change = next_status(
                    trip, now, rng,
                    pickup_drive_seconds=_pickup_seconds(trip, rng),
                    cancel_by_driver_chance=cancel_by_driver,
                    cancel_by_passenger_chance=cancel_by_passenger,
                )
                if change is None:
                    continue

                status, next_at = change
                previous = trip.status
                trip.status = status
                trip.next_change_at = next_at

                actual_duration_s = None
                fare_final = None

                if status == "in_progress":
                    trip.started_at = now
                elif status == "completed":
                    actual_duration_s = int((now - trip.started_at).total_seconds()) \
                        if trip.started_at else trip.predicted_duration_s
                    # The final price recomputes the time part from the real
                    # duration, so traffic actually costs money.
                    fare_final = pricing.fare(
                        base_fare, per_km, per_minute,
                        trip.route_km, actual_duration_s, trip.surge_multiplier,
                    )
                    completed += 1

                announce(producer, trip, status, now, actual_duration_s, fare_final)
                _write_status(trip, status, actual_duration_s, fare_final)

                if status in FINISHED:
                    # The trip is over: forget it here and let the live state
                    # go, so nothing keeps reading a finished trip.
                    active.pop(trip.trip_id, None)
                    redis.delete(redis_client.trip_active_key(trip.trip_id))
                else:
                    store_live_state(redis, trip, now, active_ttl)

                log.debug(
                    "trip moved on",
                    extra={"trip_id": trip.trip_id, "from": previous, "to": status},
                )

            # --- 4. keep running trips fresh ---------------------------
            for trip in active.values():
                if trip.status == "in_progress":
                    store_live_state(redis, trip, now, active_ttl)

            if taken:
                log.info(
                    "tick",
                    extra={
                        "requests_taken": taken,
                        "active_now": len(active),
                        "matched_total": matched,
                        "no_driver_total": unmatched,
                        "completed_total": completed,
                    },
                )

            elapsed = time.monotonic() - started
            if shutdown.wait(max(tick_seconds - elapsed, 0.0)):
                break

    finally:
        producer.flush()
        consumer.close()
        log.info(
            "stopped",
            extra={"matched": matched, "no_driver": unmatched, "completed": completed},
        )

    return 0


def assign(request: dict, redis, producer: AvroTopicProducer, now: datetime,
           period: str, rng: random.Random, grid: CityGrid,
           search_radius_km: float, base_fare: float, per_km: float,
           per_minute: float) -> ActiveTrip | None:
    """Give one request a driver, a route and a price.

    Returns None when the trip cannot be served, having already recorded and
    announced that. "Nobody was available" is a result worth keeping: it is
    the number that says the fleet is too small at this hour.
    """
    trip_id = request["trip_id"]
    pickup_lat = float(request["pickup_lat"])
    pickup_lon = float(request["pickup_lon"])
    dropoff_lat = float(request["dropoff_lat"])
    dropoff_lon = float(request["dropoff_lon"])
    zone_id = request.get("pickup_zone_id") or grid.zone_of(pickup_lat, pickup_lon)

    driver_id = find_driver(redis, pickup_lat, pickup_lon, search_radius_km)
    if driver_id is None:
        _no_driver(producer, request, trip_id, zone_id, now)
        return None

    # The expensive part: a real path over the street network, weighted by
    # how congested each segment is at this time of day.
    computed = routing.route(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon, period)
    if computed is None:
        # The two points are not connected in the imported map. Treated the
        # same as having nobody to send: the rider cannot be served.
        log.warning("no route found", extra={"trip_id": trip_id})
        _no_driver(producer, request, trip_id, zone_id, now)
        return None

    route_km, predicted_s, route_wkt = computed
    surge = pricing.surge_for(redis, zone_id, period)
    estimate = pricing.fare(base_fare, per_km, per_minute, route_km, predicted_s, surge)

    trip = ActiveTrip(
        trip_id=trip_id,
        rider_id=request["rider_id"],
        driver_id=driver_id,
        pickup_lat=pickup_lat, pickup_lon=pickup_lon,
        dropoff_lat=dropoff_lat, dropoff_lon=dropoff_lon,
        pickup_zone_id=zone_id,
        route_km=route_km,
        predicted_duration_s=predicted_s,
        surge_multiplier=surge,
        fare_estimate=estimate,
        status="matched",
        next_change_at=first_change_at(now, rng),
    )

    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                UPDATE_ASSIGNED,
                {
                    "trip_id": trip_id,
                    "driver_id": driver_id,
                    "status": "matched",
                    "route_wkt": route_wkt,
                    "route_km": route_km,
                    "predicted_duration_s": predicted_s,
                    "surge_multiplier": surge,
                    "fare_estimate": estimate,
                },
            )
        conn.commit()

    # Taken out of the free list at once, so no second trip can be offered to
    # this driver before driver-service notices.
    redis.zrem(redis_client.GEO_AVAILABLE_DRIVERS, driver_id)

    announce(producer, trip, "matched", now)
    return trip


def _no_driver(producer: AvroTopicProducer, request: dict, trip_id: str,
               zone_id: str, now: datetime) -> None:
    """Record and announce that nobody could take this trip."""
    producer.send(
        key=trip_id,
        value={
            "trip_id": trip_id,
            "rider_id": request["rider_id"],
            "driver_id": None,
            "status": "no_driver_found",
            "pickup_zone_id": zone_id,
            "route_km": None,
            "predicted_duration_s": None,
            "actual_duration_s": None,
            "surge_multiplier": None,
            "fare_estimate": None,
            "fare_final": None,
            "event_time": to_millis(now),
        },
    )
    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                UPDATE_STATUS,
                {
                    "trip_id": trip_id,
                    "status": "no_driver_found",
                    "actual_duration_s": None,
                    "fare_final": None,
                },
            )
        conn.commit()


def _write_status(trip: ActiveTrip, status: str, actual_duration_s: int | None,
                  fare_final: float | None) -> None:
    """Record a status change in the database."""
    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                UPDATE_STATUS,
                {
                    "trip_id": trip.trip_id,
                    "status": status,
                    "actual_duration_s": actual_duration_s,
                    "fare_final": fare_final,
                },
            )
        conn.commit()


def _pickup_seconds(trip: ActiveTrip, rng: random.Random) -> int:
    """Roughly how long the driver needs to reach the rider.

    A straight-line estimate on purpose. Routing the pickup leg as well would
    double the most expensive step in the pipeline to answer a question
    nobody reports on.
    """
    km = distance_km(trip.pickup_lat, trip.pickup_lon, trip.dropoff_lat, trip.dropoff_lon)
    return max(int(min(km, 4.0) / 20.0 * 3600 * rng.uniform(0.7, 1.4)), 60)


if __name__ == "__main__":
    sys.exit(main())
