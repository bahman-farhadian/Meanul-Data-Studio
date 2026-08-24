"""The city grid, in one place.

The city is divided into a simple grid of zones. Several components need the
same answers about it - which zone a point falls in, where the middle of a
zone is, a random point inside one - and two implementations of the same
arithmetic would quietly disagree. So the arithmetic lives here and everyone
asks this class.

A grid rather than real neighbourhoods, because every cell is then the same
size, and "this zone is busier than that one" means what it says.
"""

from dataclasses import dataclass

from nus_common import config


@dataclass(frozen=True)
class CityGrid:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    rows: int
    cols: int

    @classmethod
    def from_environment(cls) -> "CityGrid":
        """Build the grid from the CITY_* settings.

        Every component reads the same variables, so all of them draw the
        same grid. They are set once, in each component's .env.
        """
        return cls(
            min_lat=config.number("CITY_MIN_LAT", 40.49),
            max_lat=config.number("CITY_MAX_LAT", 40.92),
            min_lon=config.number("CITY_MIN_LON", -74.26),
            max_lon=config.number("CITY_MAX_LON", -73.70),
            rows=config.integer("CITY_GRID_ROWS", 6),
            cols=config.integer("CITY_GRID_COLS", 6),
        )

    @property
    def lat_step(self) -> float:
        return (self.max_lat - self.min_lat) / self.rows

    @property
    def lon_step(self) -> float:
        return (self.max_lon - self.min_lon) / self.cols

    @staticmethod
    def zone_id(row: int, col: int) -> str:
        """The id of one cell, for example z-02-05."""
        return f"z-{row:02d}-{col:02d}"

    def all_zone_ids(self) -> list[str]:
        return [
            self.zone_id(row, col)
            for row in range(self.rows)
            for col in range(self.cols)
        ]

    def bounds_of(self, zone_id: str) -> tuple[float, float, float, float]:
        """The (south, west, north, east) edges of one zone."""
        _, row_text, col_text = zone_id.split("-")
        row, col = int(row_text), int(col_text)
        south = self.min_lat + row * self.lat_step
        west = self.min_lon + col * self.lon_step
        return south, west, south + self.lat_step, west + self.lon_step

    def centre_of(self, zone_id: str) -> tuple[float, float]:
        """The middle of one zone."""
        south, west, north, east = self.bounds_of(zone_id)
        return (south + north) / 2, (west + east) / 2

    def random_point_in(self, zone_id: str, rng) -> tuple[float, float]:
        """A random point inside one zone."""
        south, west, north, east = self.bounds_of(zone_id)
        return rng.uniform(south, north), rng.uniform(west, east)

    def zone_of(self, lat: float, lon: float) -> str:
        """Which zone a point falls in.

        A point outside the city box is pulled to the nearest edge cell
        rather than refused: a driver who wandered a street too far should
        still be counted somewhere.
        """
        row = int((lat - self.min_lat) / self.lat_step)
        col = int((lon - self.min_lon) / self.lon_step)
        row = min(max(row, 0), self.rows - 1)
        col = min(max(col, 0), self.cols - 1)
        return self.zone_id(row, col)
