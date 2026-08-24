-- The three tables the platform runs on: who drives, who rides, and every
-- trip between them.
--
-- Two things to notice:
--
-- 1. Anything shaped like a document (vehicle details, phone details,
--    loose trip attributes) is stored in a jsonb column. PostgreSQL indexes
--    and queries those directly, which is why this stack has no separate
--    document database.
-- 2. Positions are PostGIS geometry columns using SRID 4326, the ordinary
--    latitude/longitude system a phone reports.

CREATE TABLE IF NOT EXISTS drivers (
    driver_id     text PRIMARY KEY,
    full_name     text        NOT NULL,
    phone         text,
    rating        numeric(2,1) NOT NULL DEFAULT 5.0,
    status        text        NOT NULL DEFAULT 'offline',
    -- The zone this driver usually works in. Used when seeding and when a
    -- driver has nowhere better to go.
    home_zone_id  text,
    last_lat      double precision,
    last_lon      double precision,
    last_seen_at  timestamptz,
    -- Model, plate, colour, seats: shaped like a document, kept as one.
    vehicle       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- Phone model, app version, battery: the same idea for the device.
    device        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT drivers_status_check
        CHECK (status IN ('offline', 'idle', 'en_route_pickup', 'on_trip'))
);

CREATE TABLE IF NOT EXISTS passengers (
    passenger_id  text PRIMARY KEY,
    full_name     text        NOT NULL,
    phone         text,
    rating        numeric(2,1) NOT NULL DEFAULT 5.0,
    home_zone_id  text,
    preferences   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    device        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id              text PRIMARY KEY,
    rider_id             text        NOT NULL REFERENCES passengers(passenger_id),
    -- Empty until a driver is matched, and it stays empty forever when
    -- nobody could be found.
    driver_id            text        REFERENCES drivers(driver_id),
    status               text        NOT NULL,

    pickup_point         geometry(Point, 4326)      NOT NULL,
    dropoff_point        geometry(Point, 4326)      NOT NULL,
    pickup_zone_id       text,
    dropoff_zone_id      text,
    -- The path pgRouting chose, kept so it can be drawn on a map later.
    -- Plain geometry rather than LineString: joining the chosen road
    -- segments usually gives one continuous line, but where the imported
    -- map has a gap it gives several pieces, and refusing to store the
    -- route at all would be the worse answer.
    route                geometry(Geometry, 4326),
    route_km             double precision,

    -- What the route calculation promised, and what really happened.
    predicted_duration_s integer,
    actual_duration_s    integer,

    -- Price. The estimate is fixed at assignment; the final fare recomputes
    -- the time part from the real duration. The multiplier is stored so the
    -- price can be explained afterwards.
    surge_multiplier     numeric(4,2),
    fare_estimate        numeric(10,2),
    fare_final           numeric(10,2),

    attributes           jsonb       NOT NULL DEFAULT '{}'::jsonb,

    requested_at         timestamptz NOT NULL,
    matched_at           timestamptz,
    accepted_at          timestamptz,
    started_at           timestamptz,
    ended_at             timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT trips_status_check CHECK (status IN (
        'requested', 'matched', 'accepted', 'en_route_pickup', 'in_progress',
        'completed', 'cancelled_by_passenger', 'cancelled_by_driver',
        'no_driver_found'
    ))
);
