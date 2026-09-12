"""Every entity id format used in this stack, in one place.

Whoever mints an id lives here, so that bootstrap's seeded history and any
live service that creates a new driver, passenger or trip later always agree
on the shape. That agreement matters beyond readability: the ClickHouse
schema stores these columns as FixedString, not String, which means the
length below is not just documentation - a differently-shaped id would fail
to write at all. Changing a format here is a schema migration, not a
one-line edit.

    driver_id / passenger_id   drv-0000001 / psg-0000001   11 characters
    trip_id                    trp-20250824-a1b2c3d4       21 characters

zone_id is deliberately not on this list: it is NYC TLC's own LocationID
("1".."263"), not an id this codebase mints, so it is not fixed-width and
is stored as plain text/String throughout (Postgres text, ClickHouse
LowCardinality(String)) rather than following the FixedString convention
above.
"""

import random
from datetime import datetime

DRIVER_ID_LENGTH = 11
PASSENGER_ID_LENGTH = 11
TRIP_ID_LENGTH = 21


def driver_id(number: int) -> str:
    """drv-0000001. `number` must stay under 10,000,000 to hold the width.

    Was 6 digits/10 characters total until a real run confirmed live:
    SEED_PASSENGERS=1,500,000 (7 digits) already exceeded the old 6-digit
    width, and ClickHouse's FixedString(10) columns rejected the resulting
    11-character id outright rather than truncating it silently. Widened
    here and in every e-infra-clickhouse/ddl/*.sql FixedString(10) column
    to match - SEED_DRIVERS is under 1,000,000 today, but this stays
    matched to passenger_id's own width on purpose: two ids of different
    lengths sharing a schema convention is the kind of thing that looks
    fine until one of them quietly grows past its own limit again.
    """
    return f"drv-{number:07d}"


def passenger_id(number: int) -> str:
    """psg-0000001. `number` must stay under 10,000,000 to hold the width."""
    return f"psg-{number:07d}"


def new_trip_id(requested_at: datetime, rng: random.Random | None = None) -> str:
    """trp-20250824-a1b2c3d4: the request date plus 8 random hex digits.

    `rng` is optional and exists for bootstrap's seeded history, where the
    same settings must produce the same ids on every run. A live service
    minting a real trip id should call this with no `rng` - it then draws
    from the process-wide random module, which is what a live id should do.
    """
    source = rng if rng is not None else random
    return f"trp-{requested_at.strftime('%Y%m%d')}-{source.getrandbits(32):08x}"
