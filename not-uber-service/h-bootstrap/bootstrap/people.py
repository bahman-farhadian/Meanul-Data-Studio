"""Creating the drivers and passengers the platform starts with.

Faker is used so the records look like records: real-looking names, phone
numbers, car models. That matters more than it sounds - a dashboard full of
"user_000123" teaches nothing, and a demo with believable rows is easier to
reason about.

The random generator is seeded, so the same settings always produce the same
people. A run that can be repeated exactly is a run that can be debugged.

Real fleet scale (up to ~106,000 drivers, ~1,500,000 passengers) means
millions of Faker calls - confirmed live to peg one CPU core for several
minutes with nothing else happening, once the database-round-trip cost
(see zones.build_road_point_pools) was already fixed. Faker generation is
pure CPU work, not I/O, so it needs real processes to actually go faster -
threads would still serialize behind the GIL. Each chunk gets its own
Faker instance and its own random.Random, seeded from the chunk's own
start index rather than shared: independent of every other chunk, and of
how many workers there are, so the same seed_value and worker count always
reproduce the same fleet (a different worker count changes the specific
values generated, the same way a different SEED_DRIVERS would - the
guarantee is "these settings always produce these people", not "the exact
values a different partitioning would have produced").
"""

import random
from concurrent.futures import ProcessPoolExecutor

from faker import Faker

from nus_common import postgres, redis_client, routing
from nus_common.ids import driver_id, passenger_id
from nus_common.logging import get_logger

from bootstrap import zones
from bootstrap.settings import Settings

log = get_logger(__name__)

# One make/model list and a seat count per tier - an XL car is a real
# bigger vehicle, not the same sedan with a different label.
VEHICLE_MAKES = {
    "economy": (
        [("Toyota", "Prius"), ("Toyota", "Camry"), ("Honda", "Accord"),
         ("Ford", "Escape"), ("Hyundai", "Sonata"), ("Nissan", "Altima"),
         ("Tesla", "Model 3"), ("Chevrolet", "Malibu")],
        4,
    ),
    "xl": (
        [("Toyota", "Sienna"), ("Honda", "Odyssey"),
         ("Chevrolet", "Suburban"), ("Ford", "Explorer")],
        6,
    ),
    "premium": (
        [("BMW", "5 Series"), ("Mercedes-Benz", "E-Class"),
         ("Audi", "A6"), ("Tesla", "Model S")],
        4,
    ),
}
# Same fleet-composition weights passenger-service draws requests from
# (k-service-passenger/passenger_service/__main__.py's VEHICLE_TYPE_WEIGHTS)
# - a fleet shaped like demand is what makes the tier actually matchable.
VEHICLE_TYPE_WEIGHTS = [70, 20, 10]
CAR_COLOURS = ["black", "white", "silver", "grey", "blue", "red"]
PHONE_MODELS = ["iPhone 15", "iPhone 13", "Pixel 8", "Galaxy S24", "Galaxy A54"]


def _chunks(total: int, workers: int) -> list[tuple[int, int]]:
    """Split the range 1..total into up to `workers` contiguous pieces."""
    if total <= 0:
        return []
    workers = max(1, min(workers, total))
    size = -(-total // workers)  # ceiling division, no float rounding
    return [(start, min(start + size - 1, total)) for start in range(1, total + 1, size)]


def _driver_chunk(args: tuple[int, int, int, list[str]]) -> tuple[list[dict], list[dict]]:
    """Build one contiguous range of drivers and their vehicles.

    Runs in its own process (see seed() below) - grid and the road-point
    pool it needs are already built in the parent by the time this is
    spawned, and a forked child inherits that memory as-is (confirmed
    live), so this makes no database call of its own at all.
    """
    start, end, seed_value, zone_ids = args
    faker = Faker()
    Faker.seed(f"{seed_value}:drivers:{start}")
    rng = random.Random(f"{seed_value}:drivers:{start}")

    drivers = []
    vehicles = []
    for number in range(start, end + 1):
        this_driver_id = driver_id(number)
        vehicle_type = rng.choices(redis_client.VEHICLE_TYPES, VEHICLE_TYPE_WEIGHTS)[0]
        makes, seats = VEHICLE_MAKES[vehicle_type]
        make, model = rng.choice(makes)
        home = rng.choice(zone_ids)
        lat, lon = zones.pooled_road_point_in_zone(home, rng)
        drivers.append(
            {
                "driver_id": this_driver_id,
                "full_name": faker.name(),
                "phone": faker.msisdn(),
                # Most drivers are good; a few are not. A flat 5.0 for
                # everybody would make every rating chart useless.
                "rating": round(rng.triangular(3.5, 5.0, 4.8), 1),
                "home_zone_id": home,
                "last_lat": lat,
                "last_lon": lon,
                "device": {
                    "model": rng.choice(PHONE_MODELS),
                    "app_version": f"4.{rng.randint(0, 9)}.{rng.randint(0, 9)}",
                },
            }
        )
        vehicles.append(
            {
                "vehicle_id": f"veh-{number:06d}",
                "driver_id": this_driver_id,
                "vehicle_type": vehicle_type,
                "seats": seats,
                "make": make,
                "model": model,
                "year": rng.randint(2015, 2024),
                "colour": rng.choice(CAR_COLOURS),
                "plate": faker.license_plate(),
            }
        )
    return drivers, vehicles


def _passenger_chunk(args: tuple[int, int, int, list[str]]) -> list[dict]:
    """Build one contiguous range of passengers. Same shape as _driver_chunk."""
    start, end, seed_value, zone_ids = args
    faker = Faker()
    Faker.seed(f"{seed_value}:passengers:{start}")
    rng = random.Random(f"{seed_value}:passengers:{start}")

    passengers = []
    for number in range(start, end + 1):
        home = rng.choice(zone_ids)
        passengers.append(
            {
                "passenger_id": passenger_id(number),
                "full_name": faker.name(),
                "phone": faker.msisdn(),
                "rating": round(rng.triangular(4.0, 5.0, 4.9), 1),
                "home_zone_id": home,
                "preferences": {
                    "quiet_ride": rng.random() < 0.25,
                    "payment": rng.choice(["card", "wallet", "cash"]),
                },
                "device": {
                    "model": rng.choice(PHONE_MODELS),
                    "app_version": f"4.{rng.randint(0, 9)}.{rng.randint(0, 9)}",
                },
            }
        )
    return passengers


def seed(settings: Settings, seed_value: int = 20250824) -> tuple[int, int]:
    """Create drivers and passengers. Existing rows are left alone."""
    # Not zones.all_zone_ids(): a driver or passenger homed in a zone whose
    # own centroid cannot reach a real road would keep re-hitting the
    # unsnapped-point fallback for as long as it exists.
    zone_ids = routing.servicable_zone_ids()

    # A home point per driver/passenger used to mean a real database round
    # trip each (nearest_road_point) - up to ~1.6 million of them at full
    # scale, sequential, nothing else running. Built once here instead;
    # see zones.build_road_point_pools's own docstring for the full story.
    # Must happen before the process pools below are created: a forked
    # worker inherits whatever is already built at fork time, not
    # anything built afterward.
    zones.build_road_point_pools(
        settings.road_point_pool_size, settings.history_routing_workers, seed_value
    )

    workers = settings.people_generation_workers

    drivers: list[dict] = []
    vehicles: list[dict] = []
    driver_args = [
        (start, end, seed_value, zone_ids) for start, end in _chunks(settings.driver_count, workers)
    ]
    if driver_args:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for chunk_drivers, chunk_vehicles in executor.map(_driver_chunk, driver_args):
                drivers.extend(chunk_drivers)
                vehicles.extend(chunk_vehicles)

    passengers: list[dict] = []
    passenger_args = [
        (start, end, seed_value, zone_ids) for start, end in _chunks(settings.passenger_count, workers)
    ]
    if passenger_args:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for chunk_passengers in executor.map(_passenger_chunk, passenger_args):
                passengers.extend(chunk_passengers)

    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO drivers (
                    driver_id, full_name, phone, rating, status,
                    home_zone_id, last_lat, last_lon, last_seen_at,
                    device
                )
                VALUES (
                    %(driver_id)s, %(full_name)s, %(phone)s, %(rating)s, 'offline',
                    %(home_zone_id)s, %(last_lat)s, %(last_lon)s, now(),
                    %(device)s
                )
                ON CONFLICT (driver_id) DO NOTHING
                """,
                [_as_json(row, "device") for row in drivers],
            )
            cur.executemany(
                """
                INSERT INTO vehicles (
                    vehicle_id, driver_id, vehicle_type, seats,
                    make, model, year, plate, colour
                )
                VALUES (
                    %(vehicle_id)s, %(driver_id)s, %(vehicle_type)s, %(seats)s,
                    %(make)s, %(model)s, %(year)s, %(plate)s, %(colour)s
                )
                ON CONFLICT (vehicle_id) DO NOTHING
                """,
                vehicles,
            )
            cur.executemany(
                """
                INSERT INTO passengers (
                    passenger_id, full_name, phone, rating,
                    home_zone_id, preferences, device
                )
                VALUES (
                    %(passenger_id)s, %(full_name)s, %(phone)s, %(rating)s,
                    %(home_zone_id)s, %(preferences)s, %(device)s
                )
                ON CONFLICT (passenger_id) DO NOTHING
                """,
                [_as_json(row, "preferences", "device") for row in passengers],
            )
        conn.commit()

    log.info(
        "people ready",
        extra={"drivers": len(drivers), "passengers": len(passengers)},
    )
    return len(drivers), len(passengers)


def _as_json(row: dict, *fields: str) -> dict:
    """Turn the dictionary fields into JSON text for the jsonb columns."""
    import json

    copy = dict(row)
    for field in fields:
        copy[field] = json.dumps(copy[field])
    return copy
