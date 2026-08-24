"""Every setting bootstrap uses, in one place.

Anything that could reasonably be turned up or down lives here and is read
from the environment, so changing the size of the simulated city never means
editing code.
"""

from dataclasses import dataclass

from nus_common import config


@dataclass(frozen=True)
class Settings:
    # --- the city -------------------------------------------------------
    # A box around New York City, in degrees. Everything generated - zones,
    # drivers, pickups - stays inside it, and the map import is cut to it.
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    # The city is divided into a simple grid of zones. 6 x 6 gives 36 zones,
    # which is enough for demand to look uneven without making every zone
    # too small to hold a trip.
    grid_rows: int
    grid_cols: int

    # --- how much to seed ------------------------------------------------
    driver_count: int
    passenger_count: int
    history_days: int
    trips_per_day: int
    # How many position reports to keep per historical trip. Real devices
    # report every few seconds; storing that for a whole week would be tens
    # of millions of rows for data nobody looks at closely.
    positions_per_trip: int

    # --- prices ----------------------------------------------------------
    base_fare: float
    per_km: float
    per_minute: float

    # --- the map ---------------------------------------------------------
    osm_url: str
    osm_md5_url: str
    osm_dir: str
    osm2pgrouting_config: str
    skip_osm: bool

    # --- switches --------------------------------------------------------
    # Set to true to run bootstrap again on a database that already has data.
    # Off by default: bootstrap is meant to be harmless when it starts a
    # second time by accident.
    force_reseed: bool


def load() -> Settings:
    return Settings(
        min_lat=config.number("CITY_MIN_LAT", 40.49),
        max_lat=config.number("CITY_MAX_LAT", 40.92),
        min_lon=config.number("CITY_MIN_LON", -74.26),
        max_lon=config.number("CITY_MAX_LON", -73.70),
        grid_rows=config.integer("CITY_GRID_ROWS", 6),
        grid_cols=config.integer("CITY_GRID_COLS", 6),

        driver_count=config.integer("SEED_DRIVERS", 800),
        passenger_count=config.integer("SEED_PASSENGERS", 5000),
        history_days=config.integer("HISTORY_DAYS", 7),
        trips_per_day=config.integer("HISTORY_TRIPS_PER_DAY", 2000),
        positions_per_trip=config.integer("HISTORY_POSITIONS_PER_TRIP", 8),

        base_fare=config.number("FARE_BASE", 3.0),
        per_km=config.number("FARE_PER_KM", 1.75),
        per_minute=config.number("FARE_PER_MINUTE", 0.45),

        osm_url=config.optional(
            "OSM_URL",
            "https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf",
        ),
        osm_md5_url=config.optional(
            "OSM_MD5_URL",
            "https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf.md5",
        ),
        osm_dir=config.optional("OSM_DIR", "/data/osm"),
        osm2pgrouting_config=config.optional(
            "OSM2PGROUTING_CONFIG", "/usr/share/osm2pgrouting/mapconfig_for_cars.xml"
        ),
        skip_osm=config.flag("SKIP_OSM_IMPORT", False),

        force_reseed=config.flag("FORCE_RESEED", False),
    )
