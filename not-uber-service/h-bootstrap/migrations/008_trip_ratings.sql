-- Real per-trip ratings, so drivers.rating/passengers.rating stop being
-- numbers nobody can trace back to an actual trip.
--
-- Two rows per completed trip (rider rates driver, driver rates rider) -
-- the same bidirectional pattern every real ride-hailing platform uses,
-- because a one-star driver deserves to know why just as much as a rider
-- deserves to see who they are getting in a car with.

CREATE TABLE IF NOT EXISTS trip_ratings (
    rating_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id     text        NOT NULL REFERENCES trips(trip_id),
    -- Who left this rating: the rider rating the driver, or the reverse.
    rater_type  text        NOT NULL,
    rating      smallint    NOT NULL,
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT trip_ratings_rater_type_check CHECK (rater_type IN ('rider', 'driver')),
    CONSTRAINT trip_ratings_rating_range_check CHECK (rating BETWEEN 1 AND 5),
    -- One rating per direction per trip - a rider doesn't get to rate the
    -- same driver on the same trip twice.
    CONSTRAINT trip_ratings_trip_rater_unique UNIQUE (trip_id, rater_type)
);

CREATE INDEX IF NOT EXISTS trip_ratings_trip_idx ON trip_ratings (trip_id);
