"""Real NYC TLC trip-record demand, loaded once and shared everywhere.

zone_demand_calibration and od_pair_calibration are built on the host by
zone-demand-prepare, from a real month of TLC's published High-Volume
For-Hire Vehicle trip records (see h-bootstrap/migrations/
012_zone_demand_calibration.sql for the full story). This is the one place
that reads them - h-bootstrap's historical week and passenger-service's
live requests both call the same functions here, so a zone that is busy
in the seeded history is exactly as busy in live traffic.

Loaded once per process and cached, the same reasoning as
nus_common.citygrid.CityGrid: a real database round trip on every request
or every historical trip would be a needless cost paid up to hundreds of
thousands of times per run.
"""

from nus_common import postgres

# A real sample month never sees every one of 263 zones at every one of
# 24 hours x 7 days - a zone/hour/day this data genuinely has no rows for
# gets this floor instead of zero, so it can still occasionally generate
# demand rather than being permanently silent. 0.1 sits well below the
# calibration data's own average of 1.0, matching "rare, not impossible".
_DEFAULT_ZONE_HOUR_WEIGHT = 0.1

# Same reasoning for an OD pair the sample month never recorded: the
# caller's own distance-decay fallback is used instead of treating an
# unseen pair as literally impossible.
_ZONE_WEIGHTS: dict[tuple[str, int, int], float] | None = None
_OD_SHARES: dict[tuple[str, str], float] | None = None

ZONE_DEMAND_SQL = "SELECT zone_id, hour_of_day, day_of_week, weight FROM zone_demand_calibration"
OD_PAIR_SQL = "SELECT pickup_zone_id, dropoff_zone_id, trip_share FROM od_pair_calibration"


def _load_zone_weights() -> dict[tuple[str, int, int], float]:
    global _ZONE_WEIGHTS
    if _ZONE_WEIGHTS is None:
        with postgres.read_connection() as conn:
            rows = postgres.fetch_all(conn, ZONE_DEMAND_SQL)
        _ZONE_WEIGHTS = {
            (row["zone_id"], int(row["hour_of_day"]), int(row["day_of_week"])): float(row["weight"])
            for row in rows
        }
    return _ZONE_WEIGHTS


def _load_od_shares() -> dict[tuple[str, str], float]:
    global _OD_SHARES
    if _OD_SHARES is None:
        with postgres.read_connection() as conn:
            rows = postgres.fetch_all(conn, OD_PAIR_SQL)
        _OD_SHARES = {
            (row["pickup_zone_id"], row["dropoff_zone_id"]): float(row["trip_share"])
            for row in rows
        }
    return _OD_SHARES


def preload() -> None:
    """Force both caches to load now, instead of on whichever call happens
    to be first.

    Only useful to a caller about to fork worker processes (h-bootstrap's
    history.generate()): a forked child inherits whatever is already
    cached at fork time, so calling this first means every worker shares
    the one real load instead of each independently querying on its own
    first zone_weight()/od_share() call - and, more importantly, all from
    the exact same snapshot rather than whichever replica each worker's
    own first query happens to land on.
    """
    _load_zone_weights()
    _load_od_shares()


def zone_weight(zone_id: str, hour: int, day_of_week: int) -> float:
    """How busy this zone actually was at this hour on this day of the
    week, in a real month of trips - 1.0 is average, the same semantic
    the code this replaces already used.

    day_of_week follows Python's own convention (date.weekday(), pandas
    .dt.dayofweek): 0 = Monday .. 6 = Sunday - the same convention the
    calibration table was built with, so a caller can pass a real
    datetime's .weekday() straight through with no translation.
    """
    return _load_zone_weights().get((zone_id, hour % 24, day_of_week % 7), _DEFAULT_ZONE_HOUR_WEIGHT)


def od_share(pickup_zone_id: str, dropoff_zone_id: str) -> float | None:
    """Share of this pickup zone's real trips that actually went to this
    dropoff zone, or None if the calibration month has no record of the
    pair - the caller's own fallback (distance decay) applies then, not a
    silent zero."""
    return _load_od_shares().get((pickup_zone_id, dropoff_zone_id))
