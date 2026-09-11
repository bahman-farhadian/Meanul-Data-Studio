-- drivers.vehicle is fully replaced by the vehicles table (005_vehicles.sql)
-- and nothing reads it any more (bootstrap/people.py now inserts straight
-- into vehicles) - dropped rather than left as dead weight.

ALTER TABLE drivers DROP COLUMN IF EXISTS vehicle;
