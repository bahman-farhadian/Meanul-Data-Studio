-- The city itself: the zones demand is measured in, and how slow each road
-- segment is.

-- Zones are the unit everything about demand is counted in. A hotspot score,
-- a surge multiplier and a dashboard row are all "per zone, per part of day".
CREATE TABLE IF NOT EXISTS city_zones (
    zone_id     text PRIMARY KEY,
    name        text        NOT NULL,
    -- The area itself, so a point can be turned into a zone with one query.
    boundary    geometry(Polygon, 4326) NOT NULL,
    -- The middle of the zone, handy for placing a marker or a driver.
    centroid    geometry(Point, 4326)   NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- How much slower than free-flow each road segment is, per part of the day.
--
-- This is what makes routing traffic-aware: pgRouting multiplies a segment's
-- travel time by its congestion factor, so the best path at 8am is not
-- necessarily the best path at 3am. h-bootstrap fills in a baseline from the
-- seeded week and city-service keeps it up to date from live traffic.
CREATE TABLE IF NOT EXISTS segment_traffic (
    -- Matches the id of a row in the pgRouting `ways` table, which is built
    -- from the map import. No foreign key: `ways` is rebuilt wholesale by
    -- the import, and a constraint would make that rebuild impossible.
    way_id            bigint      NOT NULL,
    period            text        NOT NULL,
    -- 1.0 means free flowing. 2.0 means it takes twice as long.
    congestion_factor double precision NOT NULL DEFAULT 1.0,
    -- How many observations the factor is based on, so a value built from
    -- three cars can be told apart from one built from three thousand.
    sample_count      integer     NOT NULL DEFAULT 0,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (way_id, period),
    CONSTRAINT segment_traffic_period_check
        CHECK (period IN ('night', 'morning', 'afternoon', 'evening')),
    CONSTRAINT segment_traffic_factor_check
        CHECK (congestion_factor > 0)
);
