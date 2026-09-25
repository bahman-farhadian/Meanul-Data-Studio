-- How many people are actually travelling.
--
-- passenger_count has been on the trip_requests Avro record since that
-- schema was written, and passenger-service produces it on every request -
-- but there has never been a column to put it in, so it is decoded and
-- dropped. Meanwhile vehicles.seats exists and requested_vehicle_type
-- exists, and nothing has ever checked that a party of five is not matched
-- to a four-seat economy car. See docs/schema-review.md F11.

ALTER TABLE trips ADD COLUMN IF NOT EXISTS passenger_count smallint NOT NULL DEFAULT 1;

-- Six is the largest party any tier in this simulation carries (xl seats 6,
-- see bootstrap/people.py). A request above it is a generator bug, not a
-- large group.
ALTER TABLE trips DROP CONSTRAINT IF EXISTS trips_passenger_count_check;
ALTER TABLE trips ADD CONSTRAINT trips_passenger_count_check
    CHECK (passenger_count BETWEEN 1 AND 6);
