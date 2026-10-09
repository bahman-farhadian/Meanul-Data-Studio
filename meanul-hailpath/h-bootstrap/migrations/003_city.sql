-- The city itself: the zones demand is measured in, and how slow each road
-- segment is.

-- Zones are the unit everything about demand is counted in. A hotspot score,
-- a surge multiplier and a dashboard row are all "per zone, per part of day".
--
-- These are NYC TLC's real, official Taxi Zones - the same 263 zones
-- Uber/Lyft/yellow cabs actually report trips against - not a synthetic
-- grid. h-bootstrap/lion-prepare builds city_zones_source from TLC's own
-- data once, on the host (see h-bootstrap/lion-prepare/taxi-zones.sql);
-- zones.py copies it in here, computing servicable per real zone.
CREATE TABLE IF NOT EXISTS city_zones (
    zone_id     text PRIMARY KEY,
    name        text        NOT NULL,
    -- TLC's own neighborhood name and borough for this zone - real
    -- metadata this project never had with the synthetic grid, and
    -- immediately useful for every zone-keyed query and dashboard.
    borough     text        NOT NULL,
    -- The area itself, so a point can be turned into a zone with one query.
    -- MultiPolygon, not Polygon: a handful of real zones (EWR among them)
    -- are genuinely multi-part.
    boundary    geometry(MultiPolygon, 4326) NOT NULL,
    -- The middle of the zone, handy for placing a marker or a driver.
    centroid    geometry(Point, 4326)   NOT NULL,
    -- False when the zone's own centroid cannot reach a real, connected road
    -- within MAX_SNAP_KM - set once, by h-bootstrap, right after the street
    -- graph is restored (see bootstrap/zones.py). Real TLC zones are all
    -- real, inhabited NYC neighborhoods, so this should rarely trip now -
    -- but a few (EWR's own tarmac-only areas, some Jamaica Bay zones) may
    -- still have no drivable LION segment nearby, and that's a legitimate,
    -- worth-keeping check now that it tests something real. Unservicable
    -- zones are excluded from demand generation and home-zone assignment
    -- everywhere, rather than only being discovered per-point through the
    -- max_snap_km retry - the same "verify once, trust everywhere"
    -- principle ways_vertices_pgr.on_main_network already applies one
    -- level down.
    servicable  boolean     NOT NULL DEFAULT true,
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
