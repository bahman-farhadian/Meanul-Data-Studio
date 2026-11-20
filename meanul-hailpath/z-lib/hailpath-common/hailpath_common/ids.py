"""Integer entity ids. Prefixes exist only on print/log paths.

Stored keys are integers: driver/passenger/vehicle UInt32, trip UInt64.
Display form (`drv-0000001`) is formatting, not identity.
"""

from datetime import datetime

# UInt32. 106000 licensed vehicles fit; 7-digit display still fits.
DRIVER_ID_MAX = 4_294_967_295
# UInt64 layout: YYYYMMDD * 10_000_000 + seq. 10M trips per calendar day.
TRIP_ID_DAY_SCALE = 10_000_000


def driver_id(number: int) -> int:
    """The driver number as stored. `number` is 1..UInt32."""
    n = int(number)
    if n < 1 or n > DRIVER_ID_MAX:
        raise ValueError(f"driver_id {n} is outside 1..{DRIVER_ID_MAX}")
    return n


def passenger_id(number: int) -> int:
    """The passenger number as stored. Same range as driver_id."""
    return driver_id(number)


def vehicle_id(number: int) -> int:
    """One vehicle per driver today; the number is the driver number."""
    return driver_id(number)


def new_trip_id(requested_at: datetime, seq: int) -> int:
    """UInt64: calendar day * 10_000_000 + seq.

    `seq` is unique for that UTC date (0..9_999_999). Bootstrap uses the
    trip index in the day. Live uses a Redis INCR for that date. 655_000
    trips/day is sequential, not a birthday problem.
    """
    day = int(requested_at.strftime("%Y%m%d"))
    s = int(seq)
    if not 0 <= s < TRIP_ID_DAY_SCALE:
        raise ValueError(f"trip seq {s} is outside 0..{TRIP_ID_DAY_SCALE - 1}")
    return day * TRIP_ID_DAY_SCALE + s


def format_driver_id(number: int) -> str:
    return f"drv-{int(number):07d}"


def format_passenger_id(number: int) -> str:
    return f"psg-{int(number):07d}"


def format_vehicle_id(number: int) -> str:
    return f"veh-{int(number):06d}"


def format_trip_id(trip_id: int) -> str:
    n = int(trip_id)
    day, seq = divmod(n, TRIP_ID_DAY_SCALE)
    return f"trp-{day}-{seq:07d}"
