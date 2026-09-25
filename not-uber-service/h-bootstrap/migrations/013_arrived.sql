-- The moment the driver reached the pickup point.
--
-- Today en_route_pickup ends and in_progress begins in one transition
-- (l-service-dispatch/dispatch_service/trips.py), so the car reaching the
-- kerb is never recorded. Uber's own dispatch documentation names this
-- state - "the driver has arrived or is within 0.2 miles of the pickup
-- point" - and it is the state the whole wait-time half of the domain
-- hangs off: rider wait at pickup is started_at - arrived_at, and a
-- cancellation counts as post-arrival exactly when arrived_at is set.
--
-- Without it, the cancellation reasons 009 already ships (rider_no_show,
-- wait_too_long) can only ever be asserted by the generator, never checked
-- against the data. See docs/schema-review.md F2.

ALTER TABLE trips ADD COLUMN IF NOT EXISTS arrived_at timestamptz;

-- 'arrived' is APPENDED to the set, never inserted in the middle. The
-- ClickHouse copy of this set is an Enum8 with explicit numbers, and a
-- renumbering there would reinterpret every status already on disk.
ALTER TABLE trips DROP CONSTRAINT IF EXISTS trips_status_check;
ALTER TABLE trips ADD CONSTRAINT trips_status_check CHECK (status IN (
    'requested', 'matched', 'accepted', 'en_route_pickup', 'in_progress',
    'completed', 'cancelled_by_passenger', 'cancelled_by_driver',
    'no_driver_found', 'arrived'
));

-- A trip cannot have reached the kerb before the driver accepted it, and
-- cannot have started before it arrived. Cheap to state, and it turns a
-- state-machine bug in dispatch into a constraint violation rather than a
-- silently impossible row that only shows up as a negative wait time on a
-- dashboard months later.
ALTER TABLE trips DROP CONSTRAINT IF EXISTS trips_arrival_order_check;
ALTER TABLE trips ADD CONSTRAINT trips_arrival_order_check CHECK (
    arrived_at IS NULL
    OR (
        (accepted_at IS NULL OR arrived_at >= accepted_at)
        AND (started_at IS NULL OR started_at >= arrived_at)
    )
);
