"""Everything dispatch writes to Redis must survive json.dumps.

This exists because it did not. Making fare_estimate a Decimal - correct
everywhere else, and the whole point of the money change - took
dispatch-service down on every single trip:

    TypeError: Object of type Decimal is not JSON serializable

Nothing caught it. The unit tests never built an ActiveTrip, and the
contract tests only compare schemas across stores, so the first sign was a
restart loop on the server with three messages on a topic that should have
had thousands. A type that is right in PostgreSQL, right in Avro and right
in ClickHouse can still be wrong at a json.dumps, and that boundary now has
a test of its own.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

DISPATCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISPATCH))

from dispatch_service.__main__ import live_state_payload  # noqa: E402
from dispatch_service.trips import ActiveTrip  # noqa: E402

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)


def _trip(**overrides) -> ActiveTrip:
    fields = {
        "trip_id": "trp-00000000000000001",
        "rider_id": "psg-0000001",
        "driver_id": "drv-0000001",
        "pickup_lat": 40.75, "pickup_lon": -73.98,
        "dropoff_lat": 40.71, "dropoff_lon": -74.01,
        "driver_lat": 40.76, "driver_lon": -73.97,
        "pickup_zone_id": "161", "dropoff_zone_id": "237",
        "route_km": 7.4,
        "predicted_duration_s": 880,
        "surge_multiplier": 1.35,
        # The type the live service actually produces - money() returns a
        # Decimal, and assign() puts that straight onto the trip.
        "fare_estimate": Decimal("24.80"),
        "status": "matched",
        "next_change_at": NOW,
    }
    fields.update(overrides)
    return ActiveTrip(**fields)


def test_live_state_is_json_serializable_with_decimal_money():
    payload = live_state_payload(_trip(), NOW)
    # The assertion is that this does not raise. json.dumps is the exact
    # call store_live_state makes.
    encoded = json.dumps(payload)
    assert json.loads(encoded)["trip_id"] == "trp-00000000000000001"


def test_live_state_money_survives_as_a_number():
    """float, not str: a reader that does arithmetic must not get "24.80"."""
    payload = live_state_payload(_trip(), NOW)
    assert payload["fare_estimate"] == 24.80
    assert isinstance(payload["fare_estimate"], float)


def test_live_state_handles_a_trip_with_no_quote_yet():
    payload = live_state_payload(_trip(fare_estimate=None), NOW)
    assert payload["fare_estimate"] is None
    json.dumps(payload)


def test_no_value_in_the_payload_needs_a_custom_encoder():
    """Catches the next type that is right in a store and wrong on the wire.

    json.dumps(default=...) is deliberately not used in store_live_state: a
    default would have silently stringified the Decimal instead of failing,
    and a fare arriving as "24.80" where a number was expected is a worse
    failure than a loud one.
    """
    payload = live_state_payload(_trip(), NOW)
    allowed = (str, int, float, bool, type(None))
    offenders = {
        key: type(value).__name__
        for key, value in payload.items()
        if not isinstance(value, allowed)
    }
    assert offenders == {}, f"not JSON-native: {offenders}"
