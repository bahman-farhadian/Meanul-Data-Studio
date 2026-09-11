"""Star ratings, and the running averages they feed.

Every completed trip gets two ratings - the rider rating the driver, the
driver rating the rider - written to trip_ratings. drivers.rating and
passengers.rating are not free-floating numbers: they are recomputed from
those rows right after, so the number on a profile always matches what the
trip history actually says.
"""

import random

from nus_common import postgres
from nus_common.logging import get_logger

log = get_logger(__name__)

INSERT_RATING = """
    INSERT INTO trip_ratings (trip_id, rater_type, rating)
    VALUES (%(trip_id)s, %(rater_type)s, %(rating)s)
    ON CONFLICT (trip_id, rater_type) DO NOTHING
"""

UPDATE_DRIVER_RATING = """
    UPDATE drivers SET rating = (
        SELECT round(avg(tr.rating)::numeric, 1)
          FROM trip_ratings tr JOIN trips t ON t.trip_id = tr.trip_id
         WHERE t.driver_id = %(driver_id)s AND tr.rater_type = 'rider'
    )
    WHERE driver_id = %(driver_id)s
"""

UPDATE_PASSENGER_RATING = """
    UPDATE passengers SET rating = (
        SELECT round(avg(tr.rating)::numeric, 1)
          FROM trip_ratings tr JOIN trips t ON t.trip_id = tr.trip_id
         WHERE t.rider_id = %(rider_id)s AND tr.rater_type = 'driver'
    )
    WHERE passenger_id = %(rider_id)s
"""


def rate_and_maintain(trip_id: str, rider_id: str, driver_id: str, rng: random.Random) -> None:
    """Rate a just-completed trip in both directions, then refresh both averages.

    Most trips go fine, so most ratings cluster near the top - the same
    triangular shape people.py already uses for a driver's starting rating.
    """
    rider_gives = _star(rng)
    driver_gives = _star(rng)

    with postgres.write_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(INSERT_RATING, {"trip_id": trip_id, "rater_type": "rider", "rating": rider_gives})
            cur.execute(INSERT_RATING, {"trip_id": trip_id, "rater_type": "driver", "rating": driver_gives})
            cur.execute(UPDATE_DRIVER_RATING, {"driver_id": driver_id})
            cur.execute(UPDATE_PASSENGER_RATING, {"rider_id": rider_id})
        conn.commit()


def _star(rng: random.Random) -> int:
    return round(rng.triangular(3, 5, 4.7))
