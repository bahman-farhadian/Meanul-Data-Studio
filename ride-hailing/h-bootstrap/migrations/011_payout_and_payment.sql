-- What the driver actually took home, and how the rider paid - both
-- decided at the same point fare_final is (trip completion), and both
-- currently missing: fares vanish today with no driver-vs-platform split
-- and no record of the payment method at all, which every real platform
-- tracks.

ALTER TABLE trips ADD COLUMN IF NOT EXISTS driver_payout numeric(10, 2);
ALTER TABLE trips ADD COLUMN IF NOT EXISTS payment_method text;

ALTER TABLE trips DROP CONSTRAINT IF EXISTS trips_payment_method_check;
ALTER TABLE trips ADD CONSTRAINT trips_payment_method_check
    CHECK (payment_method IS NULL OR payment_method IN ('card', 'wallet', 'cash'));
