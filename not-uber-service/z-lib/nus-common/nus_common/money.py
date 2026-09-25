"""Money, as one representation from end to end.

Every amount in this stack is a Decimal with exactly two places:
`numeric(10, 2)` in PostgreSQL, Avro `decimal(10, 2)` on the wire,
`Decimal64(2)` in ClickHouse. This module is the one place a number becomes
money, so the rounding rule is stated once instead of being re-decided at
each call site.

Why not float, when a fare fits in one easily:

- Float addition is not associative. Summing the same 100,000 values
  forward and then backward on ClickHouse 24.8 gives 386991.20000000513 and
  386991.2000000049 - measured, not assumed. The rollups sum these columns
  inside background merges, in an order nobody controls.
- The drift is about 1e-9 relative, so this is not usually a wrong cent.
  What it is: a revenue figure that does not equal itself across two
  replicas, never reconciles exactly against PostgreSQL, and renders with a
  float tail on a dashboard.

fastavro refuses a float where a decimal is declared, which is what makes
the wire half of this impossible to get wrong quietly - but the error
arrives at serialization time, several frames from the arithmetic that
caused it. Converting here, at the point the amount is computed, is what
keeps that error legible.
"""

from decimal import ROUND_HALF_UP, Decimal

# Two places, matching numeric(10, 2) exactly. Named rather than inlined so
# that the day a currency with three minor digits appears, there is one
# place to look.
CENTS = Decimal("0.01")


def money(amount: float | int | str | Decimal) -> Decimal:
    """Turn a computed amount into a storable one, rounded to whole cents.

    ROUND_HALF_UP, not Python's banker's rounding default: a fare of
    12.225 charges 12.23, which is what a rider reading a receipt expects
    and what every pricing description in this project already implies.
    Decimal's own default (ROUND_HALF_EVEN) would charge 12.22 and be
    right half the time by design.

    A float argument is converted through `str` on purpose. Decimal(12.22)
    captures the binary approximation (12.2199999999999997513...), while
    Decimal(str(12.22)) is exactly 12.22 - which is the number the caller
    meant.
    """
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def money_or_none(amount: float | int | str | Decimal | None) -> Decimal | None:
    """`money`, but passes None through.

    Most money on a trip is genuinely absent until the trip ends - a fare
    that has not been charged is not zero, and storing it as zero would put
    unearned revenue into every rollup.
    """
    return None if amount is None else money(amount)
