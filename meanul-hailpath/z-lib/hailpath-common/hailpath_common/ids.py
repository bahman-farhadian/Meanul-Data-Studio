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


# 62 symbols rather than 16, over the same eight characters. The width is
# NOT a style choice: trip_id is FixedString(21) in six ClickHouse columns,
# so eight is all the room there is without a schema migration, and the
# alphabet is the only lever that is not one.
#
# Eight HEX digits is 32 bits - 4.29 billion ids per day - which sounds
# ample and is not. Ids are scoped per day, so the birthday bound applies
# to one day's trips: N^2/2M expected collisions gives 0.5 at 65,500 trips
# a day, 12.5 at 327,500, and 50 at the 655,000 this project targets. It
# was never a remote risk at full scale, it was a certainty.
#
# It cost a 141-minute bootstrap at the 50% scale rung, failing on
# dispatch_offers_one_accepted_idx: two different trips held one id, so one
# trip appeared to have accepted two offers. The 10% rung before it had a
# 39% chance of the same failure and simply got lucky.
#
# Base62 over eight characters is 62^8 = 2.18e14, about 50,000x the space,
# which puts a whole seven-day full-scale seed under one percent.
_TRIP_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_TRIP_ID_SUFFIX_LENGTH = 8


def new_trip_id(requested_at: datetime, rng: random.Random | None = None) -> str:
    """trp-20250824-a1B2c3D4: the request date plus 8 base62 characters.

    `rng` is optional and exists for bootstrap's seeded history, where the
    same settings must produce the same ids on every run. A live service
    minting a real trip id should call this with no `rng` - it then draws
    from the process-wide random module, which is what a live id should do.

    choices(), not getrandbits() reduced modulo the alphabet: 2^k is never
    a multiple of 62, so the modulo would bias the low symbols and quietly
    give back some of the space this exists to buy.
    """
    source = rng if rng is not None else random
    suffix = "".join(source.choices(_TRIP_ID_ALPHABET, k=_TRIP_ID_SUFFIX_LENGTH))
    return f"trp-{requested_at.strftime('%Y%m%d')}-{suffix}"
