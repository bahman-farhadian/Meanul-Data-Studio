"""A restart can publish a position before the opening path pass returns."""

from driver_service.__main__ import install_opening_paths
from driver_service.fleet import IDLE, Driver


class _Grid:
    def zone_of(self, _lat, _lon):
        return "1"


class _Producer:
    def __init__(self):
        self.sent = []

    def send(self, **kwargs):
        self.sent.append(kwargs)


def test_opening_paths_publish_before_the_pass_returns(monkeypatch):
    import driver_service.__main__ as driver_main

    order = []

    def drive_path(*_args, **_kwargs):
        order.append("path")
        return [(40.7500, -73.9900), (40.7510, -73.9890)]

    def pooled(*_args, **_kwargs):
        return (40.7510, -73.9890)

    monkeypatch.setattr(driver_main.routing, "drive_path", drive_path)
    monkeypatch.setattr(driver_main.routing, "pooled_road_point_in_zone", pooled)

    driver = Driver("drv-0000001", 40.7500, -73.9900, "1", status=IDLE)
    producer = _Producer()
    install_opening_paths([driver], producer, _Grid(), "morning", __import__("random").Random(1))

    assert order == ["path"]
    assert len(producer.sent) == 1
    assert producer.sent[0]["key"] == "drv-0000001"
    assert producer.sent[0]["value"]["driver_id"] == "drv-0000001"
