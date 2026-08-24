"""Creating the drivers and passengers the platform starts with.

Faker is used so the records look like records: real-looking names, phone
numbers, car models. That matters more than it sounds - a dashboard full of
"user_000123" teaches nothing, and a demo with believable rows is easier to
reason about.

The random generator is seeded, so the same settings always produce the same
people. A run that can be repeated exactly is a run that can be debugged.
"""

import random

from faker import Faker

from nus_common import postgres
from nus_common.logging import get_logger

from bootstrap import zones
from bootstrap.settings import Settings

log = get_logger(__name__)

CAR_MAKES = [
    ("Toyota", "Prius"), ("Toyota", "Camry"), ("Honda", "Accord"),
    ("Ford", "Escape"), ("Hyundai", "Sonata"), ("Nissan", "Altima"),
    ("Tesla", "Model 3"), ("Chevrolet", "Malibu"),
]
CAR_COLOURS = ["black", "white", "silver", "grey", "blue", "red"]
PHONE_MODELS = ["iPhone 15", "iPhone 13", "Pixel 8", "Galaxy S24", "Galaxy A54"]


def driver_id(number: int) -> str:
    return f"drv-{number:06d}"


def passenger_id(number: int) -> str:
    return f"psg-{number:06d}"


def seed(settings: Settings, seed_value: int = 20250824) -> tuple[int, int]:
    """Create drivers and passengers. Existing rows are left alone."""
    faker = Faker()
    Faker.seed(seed_value)
    rng = random.Random(seed_value)

    zone_ids = zones.all_zone_ids(settings)

    drivers = []
    for number in range(1, settings.driver_count + 1):
        make, model = rng.choice(CAR_MAKES)
        home = rng.choice(zone_ids)
        lat, lon = zones.random_point_in_zone(settings, home, rng)
        drivers.append(
            {
                "driver_id": driver_id(number),
                "full_name": faker.name(),
                "phone": faker.msisdn(),
                # Most drivers are good; a few are not. A flat 5.0 for
                # everybody would make every rating chart useless.
                "rating": round(rng.triangular(3.5, 5.0, 4.8), 1),
                "home_zone_id": home,
                "last_lat": lat,
                "last_lon": lon,
                "vehicle": {
                    "make": make,
                    "model": model,
                    "year": rng.randint(2015, 2024),
                    "colour": rng.choice(CAR_COLOURS),
                    "plate": faker.license_plate(),
                    "seats": 4,
                    "electric": make == "Tesla",
                },
                "device": {
                    "model": rng.choice(PHONE_MODELS),
                    "app_version": f"4.{rng.randint(0, 9)}.{rng.randint(0, 9)}",
                },
            }
        )

    passengers = []
    for number in range(1, settings.passenger_count + 1):
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

    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO drivers (
                    driver_id, full_name, phone, rating, status,
                    home_zone_id, last_lat, last_lon, last_seen_at,
                    vehicle, device
                )
                VALUES (
                    %(driver_id)s, %(full_name)s, %(phone)s, %(rating)s, 'offline',
                    %(home_zone_id)s, %(last_lat)s, %(last_lon)s, now(),
                    %(vehicle)s, %(device)s
                )
                ON CONFLICT (driver_id) DO NOTHING
                """,
                [_as_json(row, "vehicle", "device") for row in drivers],
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
