"""City watches trip_lifecycle. It does not ingest the GPS firehose."""

from pathlib import Path

from city_service.__main__ import WATCHED_TOPICS


def test_city_does_not_subscribe_to_driver_location():
    assert WATCHED_TOPICS == ["trip_lifecycle"]
    source = (Path(__file__).resolve().parents[1] / "city_service" / "__main__.py").read_text()
    assert "_note_driver" not in source
    assert "refresh_free_from_geo" in source
    assert "apply_last_known_speeds" in source
    assert 'topic == "driver_location"' not in source
