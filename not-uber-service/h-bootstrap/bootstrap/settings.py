"""Every setting bootstrap uses, in one place.

Anything that could reasonably be turned up or down lives here and is read
from the environment, so changing the size of the simulated city never means
editing code.
"""

from dataclasses import dataclass

from nus_common import config


@dataclass(frozen=True)
class Settings:
    # --- how much to seed ------------------------------------------------
    driver_count: int
    passenger_count: int
    # Generating a driver/passenger record is pure CPU work (Faker's
    # name/phone/plate generation, mostly) - real processes, not threads,
    # since Python's GIL would otherwise serialize it regardless of thread
    # count. Matches BOOTSTRAP_CPUS by default: spawning more OS processes
    # than the container's own cgroup quota allows just adds contention,
    # not speed.
    people_generation_workers: int
    history_days: int
    trips_per_day: int
    # Deciding one historical trip's pickup/dropoff/outcome before routing
    # is pure CPU work too (zone-weight and OD-share lookups, distance
    # decay - a handful of per-zone loops, run once per trip) - real
    # processes for the same reason people_generation_workers is. Separate
    # from history_routing_workers on purpose: that one is an I/O-bound
    # thread pool waiting on Postgres, this one is CPU-bound and needs
    # real cores, not just concurrent connections.
    history_generation_workers: int
    # How many position reports to keep per historical trip. Real devices
    # report every few seconds; storing that for a whole week would be tens
    # of millions of rows for data nobody looks at closely.
    positions_per_trip: int
    # Historical trips are routed for real, through the same pgRouting query
    # dispatch-service uses live - one Postgres round trip per trip, roughly
    # 50-150ms each. This many run at once, each on its own pooled
    # connection, so routing hundreds of thousands of trips does not run
    # one at a time.
    history_routing_workers: int
    # How many real, road-snapped points to precompute per zone before
    # assigning driver/passenger home locations and historical trip
    # pickups/dropoffs - see zones.build_road_point_pools's own docstring
    # for why this exists at all.
    road_point_pool_size: int

    # --- prices ----------------------------------------------------------
    base_fare: float
    per_km: float
    per_minute: float
    # What the platform keeps from every fare; the rest is the driver's
    # payout. Same default as l-service-dispatch/.env.example, so the
    # historical week and live trips split fares the same way.
    platform_commission_pct: float

    # --- the map ---------------------------------------------------------
    # The routable graph itself is built once, on the host, by
    # `make lion-prepare` (h-bootstrap/lion-prepare/) - this is just where
    # the finished dump is mounted for import_map() to restore.
    lion_dir: str
    skip_map_import: bool

    # --- switches --------------------------------------------------------
    # Set to true to run bootstrap again on a database that already has data.
    # Off by default: bootstrap is meant to be harmless when it starts a
    # second time by accident.
    force_reseed: bool


def load() -> Settings:
    return Settings(
        # Real NYC HVFHS scale (NYC TLC 2024 Annual Report: ~106,000
        # licensed vehicles, ~655,000 trips/day) - this project's actual
        # deployment target is Dionysus, not a laptop, so this is the
        # project's own default, matching .env.example. Override down in
        # your own .env for a smaller dev machine.
        driver_count=config.integer("SEED_DRIVERS", 106_000),
        passenger_count=config.integer("SEED_PASSENGERS", 1_500_000),
        people_generation_workers=config.integer("PEOPLE_GENERATION_WORKERS", 20),
        history_days=config.integer("HISTORY_DAYS", 7),
        trips_per_day=config.integer("HISTORY_TRIPS_PER_DAY", 655_000),
        history_generation_workers=config.integer("HISTORY_GENERATION_WORKERS", 20),
        positions_per_trip=config.integer("HISTORY_POSITIONS_PER_TRIP", 8),
        history_routing_workers=config.integer("HISTORY_ROUTING_WORKERS", 18),
        road_point_pool_size=config.integer("ROAD_POINT_POOL_SIZE", 300),

        base_fare=config.number("FARE_BASE", 3.0),
        per_km=config.number("FARE_PER_KM", 1.75),
        per_minute=config.number("FARE_PER_MINUTE", 0.45),
        platform_commission_pct=config.number("PLATFORM_COMMISSION_PCT", 0.23),

        lion_dir=config.optional("LION_DIR", "/data/lion"),
        skip_map_import=config.flag("SKIP_MAP_IMPORT", False),

        force_reseed=config.flag("FORCE_RESEED", False),
    )
