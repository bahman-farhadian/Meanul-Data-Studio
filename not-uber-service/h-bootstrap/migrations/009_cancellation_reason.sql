-- Why a trip was cancelled, not just who cancelled it.
--
-- status already says cancelled_by_driver vs cancelled_by_passenger; this
-- is the reason within that, the same way a real platform's support and
-- analytics tooling needs to tell "rider never showed up" apart from
-- "driver found something better" - very different problems that would
-- otherwise look identical in the data.

ALTER TABLE trips ADD COLUMN IF NOT EXISTS cancellation_reason text;

ALTER TABLE trips DROP CONSTRAINT IF EXISTS trips_cancellation_reason_check;
ALTER TABLE trips ADD CONSTRAINT trips_cancellation_reason_check
    CHECK (cancellation_reason IS NULL OR cancellation_reason IN (
        'rider_no_show', 'driver_too_far', 'vehicle_issue',
        'changed_mind', 'found_alternative', 'wait_too_long'
    ));
