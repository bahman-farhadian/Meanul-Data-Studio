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
from datetime import UTC, datetime
from decimal import Decimal

from nus_common import config, postgres, redis_client, routing
from nus_common.citygrid import CityGrid
from nus_common.geo import day_period, distance_km, to_millis, utc_now
from nus_common.kafka import AvroTopicConsumer, AvroTopicProducer
from nus_common.lifecycle import Shutdown, wait_for_bootstrap
from nus_common.logging import get_logger, setup_logging
from nus_common.money import money, money_or_none
from nus_common.offers import Offer, run_chain

from dispatch_service import pricing, ratings
from dispatch_service.trips import ActiveTrip, first_change_at, next_status

log = get_logger(__name__)

REQUEST_TOPIC = "trip_requests"
LIFECYCLE_TOPIC = "trip_lifecycle"
OFFER_TOPIC = "dispatch_offers"

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
           arrived_at  = CASE WHEN %(status)s = 'arrived' THEN now() ELSE arrived_at END,
           started_at  = CASE WHEN %(status)s = 'in_progress' THEN now() ELSE started_at END,
           ended_at    = CASE WHEN %(status)s IN ('completed', 'cancelled_by_passenger',
                                                  'cancelled_by_driver', 'no_driver_found')
                              THEN now() ELSE ended_at END,
           actual_duration_s = COALESCE(%(actual_duration_s)s, actual_duration_s),
           fare_final = COALESCE(%(fare_final)s, fare_final),
           cancellation_reason = COALESCE(%(cancellation_reason)s, cancellation_reason),
           driver_payout = COALESCE(%(driver_payout)s, driver_payout),
           payment_method = COALESCE(%(payment_method)s, payment_method)
     WHERE trip_id = %(trip_id)s
"""

INSERT_OFFER = """
    INSERT INTO dispatch_offers (
        trip_id, driver_id, sequence, offered_at, expires_at, responded_at,
        status, eta_seconds, distance_to_pickup_m
    ) VALUES (
        %(trip_id)s, %(driver_id)s, %(sequence)s, %(offered_at)s, %(expires_at)s,
        %(responded_at)s, %(status)s, %(eta_seconds)s, %(distance_to_pickup_m)s
    )
    ON CONFLICT (trip_id, sequence) DO NOTHING
"""

# Which side cancelled shapes why - a driver who bails does so for a
# different reason than a rider who gives up waiting, and conflating them
# would make the data lie about which problem is actually happening.
#
# Split by PHASE as well as by side, because two of the six reasons are
# only possible once the car is at the kerb. rider_no_show used to be in
# the pre-arrival list, so a driver could report a no-show for a pickup
# they had not reached yet - caught by quality bar Q5 on its first real
# run against live traffic, one row in a few thousand. Same for
# wait_too_long: waiting too long needs something to have waited for.
DRIVER_CANCEL_REASONS = ["driver_too_far", "vehicle_issue"]
PASSENGER_CANCEL_REASONS = ["changed_mind", "found_alternative"]
# Only reachable from the 'arrived' state, and the state machine is what
# guarantees that rather than a comment.
DRIVER_ARRIVED_REASON = "rider_no_show"
PASSENGER_ARRIVED_REASON = "wait_too_long"


def find_candidates(
    redis, lat: float, lon: float, radius_km: float, vehicle_type: str,
    limit: int, offer_speed_kmh: float,
) -> list[tuple[str, float, float, int, int]]:
    """The nearest free drivers of the requested tier, nearest first.

    Returns (driver_id, lon, lat, eta_seconds, distance_m) per candidate.
    More than one because a driver may now refuse: the offer goes down this
    list until somebody accepts - see dispatch_service.offers.

    The ETA here is straight-line distance over an assumed city speed, NOT a
    pgRouting call. A real route per candidate would multiply this service's
    single most expensive operation by the length of the offer chain, for a
    number that only has to be good enough to show a driver and to predict
    whether they accept. The accepted driver's real pickup route is computed
    once, afterwards.

    Redis keeps one geo set per vehicle tier, updated by driver-service every
    few seconds - searching only the requested tier's set is what stops an
    economy rider being matched to an XL car (or the reverse). Nothing here
    touches PostgreSQL.
    """
    found = redis.geosearch(
        redis_client.geo_available_drivers_key(vehicle_type),
        longitude=lon, latitude=lat,
        radius=radius_km, unit="km",
        sort="ASC", count=limit,
        withcoord=True, withdist=True,
    )
    candidates: list[tuple[str, float, float, int, int]] = []
    for member, distance_km_away, (driver_lon, driver_lat) in found:
        distance_m = int(float(distance_km_away) * 1000)
        eta_seconds = int(float(distance_km_away) / max(offer_speed_kmh, 1.0) * 3600)
        candidates.append(
            (str(member), float(driver_lon), float(driver_lat), eta_seconds, distance_m)
        )
    return candidates


def announce(producer: AvroTopicProducer, trip: ActiveTrip, status: str,
             now: datetime, actual_duration_s: int | None = None,
             fare_final: Decimal | None = None,
             driver_payout: Decimal | None = None,
             payment_method: str | None = None,
             cancellation_reason: str | None = None) -> None:
    """Tell everyone that a trip has changed status.

    The message carries the trip's whole milestone clock, not just the
    status and the moment. That is what lets the warehouse keep one plain
    row per trip (trip_facts) instead of six event rows and a self-join -
    and the times are the ones the trip actually has, rather than whenever
    the pipeline happened to see each event.
    """
    producer.send(
        key=trip.trip_id,
        value={
            "trip_id": trip.trip_id,
            "rider_id": trip.rider_id,
            "driver_id": trip.driver_id,
            "status": status,
            "pickup_zone_id": trip.pickup_zone_id,
            "dropoff_zone_id": trip.dropoff_zone_id,
            "passenger_count": trip.passenger_count,
            "requested_vehicle_type": trip.requested_vehicle_type,
            "route_km": trip.route_km,
            "predicted_duration_s": trip.predicted_duration_s,
            "actual_duration_s": actual_duration_s,
            "surge_multiplier": trip.surge_multiplier,
            "fare_estimate": money_or_none(trip.fare_estimate),
            "fare_final": money_or_none(fare_final),
            "driver_payout": money_or_none(driver_payout),
            "payment_method": payment_method,
            "cancellation_reason": cancellation_reason,
            "requested_at": to_millis(trip.requested_at) if trip.requested_at else None,
            "matched_at": to_millis(trip.matched_at) if trip.matched_at else None,
            "accepted_at": to_millis(trip.accepted_at) if trip.accepted_at else None,
            "arrived_at": to_millis(trip.arrived_at) if trip.arrived_at else None,
            "started_at": to_millis(trip.started_at) if trip.started_at else None,
            "ended_at": to_millis(now) if status in FINISHED else None,
            "event_time": to_millis(now),
        },
        # Everything about one trip shares a correlation id, so a single
        # ride can be followed across trip_requests, dispatch_offers,
        # trip_lifecycle and the position streams.
        correlation_id=trip.trip_id,
    )


def announce_offers(producer: AvroTopicProducer, trip_id: str, zone_id: str,
                    surge: float, offers: list[Offer], now: datetime) -> None:
    """Publish the whole offer chain: the refusals as well as the accept.

    One message per offer, at its outcome - not two (one when offered, one
    when answered). An offer in flight is live state and belongs in Postgres
    and Redis; the warehouse wants the completed record.
    """
    for offer in offers:
        producer.send(
            key=trip_id,
            value={
                "trip_id": trip_id,
                "driver_id": offer.driver_id,
                "sequence": offer.sequence,
                "status": offer.status,
                "pickup_zone_id": zone_id,
                "eta_seconds": offer.eta_seconds,
                "distance_to_pickup_m": offer.distance_to_pickup_m,
                "surge_multiplier": surge,
                "offered_at": to_millis(offer.offered_at),
                "expires_at": to_millis(offer.expires_at),
                "responded_at": to_millis(offer.responded_at) if offer.responded_at else None,
                "event_time": to_millis(now),
            },
            correlation_id=trip_id,
        )


def record_offers(trip_id: str, offers: list[Offer]) -> None:
    """Keep the chain in PostgreSQL, where the live funnel is queried."""
    if not offers:
        return
    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                INSERT_OFFER,
                [
                    {
                        "trip_id": trip_id,
                        "driver_id": offer.driver_id,
                        "sequence": offer.sequence,
                        "offered_at": offer.offered_at,
                        "expires_at": offer.expires_at,
                        "responded_at": offer.responded_at,
                        "status": offer.status,
                        "eta_seconds": offer.eta_seconds,
                        "distance_to_pickup_m": offer.distance_to_pickup_m,
                    }
                    for offer in offers
                ],
            )
        conn.commit()


def store_live_state(redis, trip: ActiveTrip, now: datetime, ttl_seconds: int) -> None:
    """Write what the other services need to know about a running trip.

    This is the live state, not the database record: where the car is now,
    where it is going, and what the route said it would take. It carries a
    lifetime, so a trip that somehow never finishes cannot leave a key behind
    for ever.
    """
    redis.set(
        redis_client.trip_active_key(trip.trip_id),
        json.dumps(live_state_payload(trip, now)),
        ex=ttl_seconds,
    )


def live_state_payload(trip: ActiveTrip, now: datetime) -> dict:
    """What store_live_state writes, as plain data.

    Split out so it can be checked without a Redis: every value here has to
    be something json.dumps accepts, and a Decimal is not. That was not a
    theoretical concern - making fare_estimate a Decimal took dispatch down
    on every single trip with "Object of type Decimal is not JSON
    serializable", and nothing in the test suite could see it.
    """
    lat, lon = trip.current_position(now)
    return {
        "trip_id": trip.trip_id,
        "rider_id": trip.rider_id,
        "driver_id": trip.driver_id,
        "status": trip.status,
        "pickup_lat": trip.pickup_lat, "pickup_lon": trip.pickup_lon,
        "dropoff_lat": trip.dropoff_lat, "dropoff_lon": trip.dropoff_lon,
        "current_lat": lat, "current_lon": lon,
        "pickup_zone_id": trip.pickup_zone_id,
        "route_km": trip.route_km,
        "route_wkt": trip.route_wkt,
        "pickup_route_wkt": trip.pickup_route_wkt,
        "pickup_route_km": trip.pickup_route_km,
        "predicted_duration_s": trip.predicted_duration_s,
        "surge_multiplier": trip.surge_multiplier,
        # float, not the Decimal it is everywhere else. This is the
        # one place money leaves the typed path on purpose: the
        # value is live display state behind a TTL, read by nobody
        # for arithmetic (driver-service and passenger-service take
        # positions and the route from this key, never the fare),
        # and json.dumps cannot encode a Decimal at all - which is
        # how this was found, as a TypeError on every single trip.
        "fare_estimate": float(trip.fare_estimate) if trip.fare_estimate is not None else None,
    }


def main() -> int:
    setup_logging("dispatch-service")
    shutdown = Shutdown()
    rng = random.Random(config.integer("RANDOM_SEED", 20250824))
    grid = CityGrid.load()

    tick_seconds = config.number("DISPATCH_TICK_SECONDS", 1.0)
    search_radius_km = config.number("DISPATCH_SEARCH_RADIUS_KM", 5.0)
    base_fare = config.number("FARE_BASE", 3.0)
    per_km = config.number("FARE_PER_KM", 1.75)
    per_minute = config.number("FARE_PER_MINUTE", 0.45)
    commission_pct = config.number("PLATFORM_COMMISSION_PCT", 0.23)
    cancel_by_driver = config.number("CANCEL_BY_DRIVER_CHANCE", 0.06)
    cancel_by_passenger = config.number("CANCEL_BY_PASSENGER_CHANCE", 0.07)
    active_ttl = config.integer("TRIP_ACTIVE_TTL_SECONDS", 7200)
    max_per_tick = config.integer("DISPATCH_MAX_REQUESTS_PER_TICK", 50)
    # How many drivers dispatch is willing to ask before giving up on a
    # request. Five is deep enough that a thin zone produces real refusals
    # rather than instant no_driver_found, and shallow enough that a rider
    # is not left waiting through five full timeouts in the worst case.
    max_offers = config.integer("DISPATCH_MAX_OFFERS", 5)
    # The speed behind the ETA shown to a driver at offer time. A rough
    # figure on purpose - see find_candidates for why this is not a route.
    offer_speed_kmh = config.number("DISPATCH_OFFER_SPEED_KMH", 18.0)

    # Three connections: matching a driver reads the requested tier's geo
    # set (DB_DRIVER) and the pickup zone's surge score (DB_DEMAND) in the
    # same call; live trip state is its own domain (DB_TRIP). A trip touches
    # all three, which is the real, unavoidable shape of "match, price, and
    # track" - not something a single db could simplify away.
    redis_driver = redis_client.primary(redis_client.DB_DRIVER)
    redis_demand = redis_client.primary(redis_client.DB_DEMAND)
    redis_trip = redis_client.primary(redis_client.DB_TRIP)
    wait_for_bootstrap(redis_client.primary(redis_client.DB_SYSTEM), shutdown)

    producer = AvroTopicProducer(LIFECYCLE_TOPIC, "dispatch-service")
    offer_producer = AvroTopicProducer(OFFER_TOPIC, "dispatch-service")
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
                    request, redis_driver, redis_demand, redis_trip, producer, now, period, rng, grid,
                    search_radius_km, base_fare, per_km, per_minute, active_ttl,
                    offer_producer, max_offers, offer_speed_kmh,
                )
                if trip is None:
                    unmatched += 1
                    continue

                active[trip.trip_id] = trip
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
                cancellation_reason = None
                driver_payout = None
                payment_method = None

                if status == "accepted":
                    trip.accepted_at = now
                elif status == "arrived":
                    trip.arrived_at = now
                elif status == "in_progress":
                    trip.started_at = now
                elif status == "completed":
                    actual_duration_s = int((now - trip.started_at).total_seconds()) \
                        if trip.started_at else trip.predicted_duration_s
                    # The final price recomputes the time part from the real
                    # duration, so traffic actually costs money.
                    fare_final = money(pricing.fare(
                        base_fare, per_km, per_minute,
                        trip.route_km, actual_duration_s, trip.surge_multiplier,
                    ))
                    # Decimal throughout: the payout is a share of a real
                    # amount, so it is computed in the same arithmetic the
                    # amount is stored in rather than a float round-trip.
                    driver_payout = money(fare_final * (Decimal(1) - money(commission_pct)))
                    payment_method = rng.choice(["card", "wallet", "cash"])
                    completed += 1
                elif status == "cancelled_by_driver":
                    # A driver who gives up at the kerb is reporting a
                    # no-show, not a long drive - the reason has to match
                    # where in the trip it happened or the data lies.
                    cancellation_reason = (DRIVER_ARRIVED_REASON if previous == "arrived"
                                           else rng.choice(DRIVER_CANCEL_REASONS))
                elif status == "cancelled_by_passenger":
                    cancellation_reason = (PASSENGER_ARRIVED_REASON if previous == "arrived"
                                           else rng.choice(PASSENGER_CANCEL_REASONS))

                if status not in FINISHED:
                    # Redis before Kafka: driver-service used to consume
                    # matched/in_progress with an empty trip_active key, skip
                    # follow(), then ignore later events as same-leg — the
                    # car kept walking the idle chord (axis_share 0.71).
                    store_live_state(redis_trip, trip, now, active_ttl)
                announce(producer, trip, status, now, actual_duration_s, fare_final,
                         driver_payout, payment_method, cancellation_reason)
                _write_status(
                    trip, status, actual_duration_s, fare_final, cancellation_reason,
                    driver_payout, payment_method,
                )
                if status == "completed":
                    ratings.rate_and_maintain(trip.trip_id, trip.rider_id, trip.driver_id, rng)

                if status in FINISHED:
                    # The trip is over: forget it here and let the live state
                    # go, so nothing keeps reading a finished trip.
                    active.pop(trip.trip_id, None)
                    redis_trip.delete(redis_client.trip_active_key(trip.trip_id))

                log.debug(
                    "trip moved on",
                    extra={"trip_id": trip.trip_id, "from": previous, "to": status},
                )

            # --- 4. keep running trips fresh ---------------------------
            for trip in active.values():
                if trip.status == "in_progress":
                    store_live_state(redis_trip, trip, now, active_ttl)

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
        offer_producer.flush()
        consumer.close()
        log.info(
            "stopped",
            extra={"matched": matched, "no_driver": unmatched, "completed": completed},
        )

    return 0


def assign(request: dict, redis_driver, redis_demand, redis_trip, producer: AvroTopicProducer,
           now: datetime, period: str, rng: random.Random, grid: CityGrid,
           search_radius_km: float, base_fare: float, per_km: float,
           per_minute: float, active_ttl: int, offer_producer: AvroTopicProducer,
           max_offers: int, offer_speed_kmh: float) -> ActiveTrip | None:
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
    dropoff_zone_id = request.get("dropoff_zone_id") or grid.zone_of(dropoff_lat, dropoff_lon)
    # Defaults to economy: passenger-service does not send this field yet
    # (its own follow-up commit), and an old request already in flight
    # during a rolling deploy should still be matchable.
    vehicle_type = request.get("requested_vehicle_type") or "economy"

    # Surge is read before the offers go out, not after: it is one of the
    # two things that decides whether a driver accepts, so it has to be the
    # number that was actually in force when they were asked.
    surge = pricing.surge_for(redis_demand, zone_id, period)

    candidates = find_candidates(
        redis_driver, pickup_lat, pickup_lon, search_radius_km, vehicle_type,
        max_offers, offer_speed_kmh,
    )
    if not candidates:
        _no_driver(producer, offer_producer, request, trip_id, zone_id, now, [], surge)
        return None

    # The trip's own route comes BEFORE the offer chain, and the order is
    # the point rather than an optimisation. It used to come after, so a
    # route that could not be computed turned the trip into
    # no_driver_found while an accepted offer was already on record - a
    # trip nobody took, with somebody having taken it. Quality bar Q8
    # found exactly two of those in six hours of live traffic.
    #
    # Reordering is sound because this route does not depend on WHICH
    # driver accepts: it is pickup to dropoff. Only the pickup leg below
    # depends on the winner, and that one is allowed to fail without
    # invalidating the match. The failure path is also cheaper now - no
    # offers are made for a trip that was never servable.
    computed = routing.route(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon, period)
    if computed is None:
        # The two points are not connected in the imported map. Treated the
        # same as having nobody to send: the rider cannot be served.
        log.warning("no route found", extra={"trip_id": trip_id})
        _no_driver(producer, offer_producer, request, trip_id, zone_id, now, [], surge)
        return None
    route_km, predicted_s, route_wkt = computed

    # The offer chain. Every driver asked is recorded, including the ones
    # who said no - those refusals ARE the acceptance rate.
    winner, offers = run_chain(
        [(driver_id, eta, metres) for driver_id, _, _, eta, metres in candidates],
        surge, now, rng,
    )
    record_offers(trip_id, offers)
    announce_offers(offer_producer, trip_id, zone_id, surge, offers, now)

    if winner is None:
        # Everybody refused or let it lapse. This is a real outcome with a
        # real cause behind it now, not a shortcut: the chain is on record.
        _no_driver(producer, offer_producer, request, trip_id, zone_id, now, [], surge)
        return None

    driver_id, driver_lon, driver_lat = next(
        (did, lon, lat) for did, lon, lat, _, _ in candidates if did == winner
    )

    # The pickup leg, for the driver who accepted. Routing every candidate
    # would multiply this service's costliest call by the chain length, and
    # this one is allowed to come back empty - the match still stands.
    pickup_leg = routing.route(driver_lat, driver_lon, pickup_lat, pickup_lon, period)
    pickup_km, pickup_s, pickup_wkt = pickup_leg if pickup_leg else (None, None, None)
    estimate = money(pricing.fare(base_fare, per_km, per_minute, route_km, predicted_s, surge))

    trip = ActiveTrip(
        trip_id=trip_id,
        rider_id=request["rider_id"],
        driver_id=driver_id,
        pickup_lat=pickup_lat, pickup_lon=pickup_lon,
        dropoff_lat=dropoff_lat, dropoff_lon=dropoff_lon,
        driver_lat=driver_lat, driver_lon=driver_lon,
        pickup_zone_id=zone_id,
        dropoff_zone_id=dropoff_zone_id,
        route_km=route_km,
        predicted_duration_s=predicted_s,
        surge_multiplier=surge,
        fare_estimate=estimate,
        status="matched",
        next_change_at=first_change_at(now, rng),
        passenger_count=int(request.get("passenger_count") or 1),
        requested_vehicle_type=vehicle_type,
        requested_at=_as_datetime(request.get("requested_at")),
        matched_at=now,
        route_wkt=route_wkt,
        pickup_route_wkt=pickup_wkt,
        pickup_route_km=pickup_km,
        pickup_duration_s=pickup_s,
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

    # Taken out of its tier's free list at once, so no second trip can be
    # offered to this driver before driver-service notices.
    redis_driver.zrem(redis_client.geo_available_drivers_key(vehicle_type), driver_id)

    store_live_state(redis_trip, trip, now, active_ttl)
    announce(producer, trip, "matched", now)
    return trip


def _as_datetime(value) -> datetime | None:
    """A timestamp-millis field as fastavro hands it back.

    fastavro decodes timestamp-millis into an aware datetime already, so
    this is mostly a guard for a raw integer arriving from somewhere else.
    """
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)


def _no_driver(producer: AvroTopicProducer, offer_producer: AvroTopicProducer,
               request: dict, trip_id: str, zone_id: str, now: datetime,
               offers: list[Offer], surge: float | None) -> None:
    """Record and announce that nobody could take this trip.

    `offers` is the chain that got here when there was one. An unmatched
    request used to be a bare assertion; now it is the end of a search that
    can be counted, and the reason - nobody in range, or everybody said no -
    is visible in whether the chain is empty.
    """
    if offers:
        record_offers(trip_id, offers)
        announce_offers(offer_producer, trip_id, zone_id, surge or 1.0, offers, now)
    requested_at = _as_datetime(request.get("requested_at"))
    producer.send(
        key=trip_id,
        value={
            "trip_id": trip_id,
            "rider_id": request["rider_id"],
            "driver_id": None,
            "status": "no_driver_found",
            "pickup_zone_id": zone_id,
            "dropoff_zone_id": request.get("dropoff_zone_id"),
            "passenger_count": int(request.get("passenger_count") or 1),
            "requested_vehicle_type": request.get("requested_vehicle_type") or "economy",
            "route_km": None,
            "predicted_duration_s": None,
            "actual_duration_s": None,
            "surge_multiplier": surge,
            "fare_estimate": None,
            "fare_final": None,
            "driver_payout": None,
            "payment_method": None,
            "cancellation_reason": None,
            "requested_at": to_millis(requested_at) if requested_at else None,
            "matched_at": None,
            "accepted_at": None,
            "arrived_at": None,
            "started_at": None,
            "ended_at": to_millis(now),
            "event_time": to_millis(now),
        },
        correlation_id=trip_id,
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
                    "cancellation_reason": None,
                    "driver_payout": None,
                    "payment_method": None,
                },
            )
        conn.commit()


def _write_status(trip: ActiveTrip, status: str, actual_duration_s: int | None,
                  fare_final: Decimal | None, cancellation_reason: str | None = None,
                  driver_payout: Decimal | None = None,
                  payment_method: str | None = None) -> None:
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
                    "cancellation_reason": cancellation_reason,
                    "driver_payout": driver_payout,
                    "payment_method": payment_method,
                },
            )
        conn.commit()


def _pickup_seconds(trip: ActiveTrip, rng: random.Random) -> int:
    """How long the driver needs to reach the rider.

    Uses the pgRouting pickup-leg duration when assign() got one, so the
    timer matches the street path the driver actually walks. Straight-line
    fallback is only for a disconnected pickup (no path). Floor 30s so a
    driver already on the same corner does not skip en_route_pickup.
    """
    if trip.pickup_duration_s:
        return max(int(trip.pickup_duration_s * rng.uniform(0.7, 1.3)), 30)
    km = distance_km(trip.driver_lat, trip.driver_lon, trip.pickup_lat, trip.pickup_lon)
    return max(int(km / 25.0 * 3600 * rng.uniform(0.7, 1.3)), 30)


if __name__ == "__main__":
    sys.exit(main())
