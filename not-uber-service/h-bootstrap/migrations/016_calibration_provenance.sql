-- Which real TLC month these weights came from, and when they were built.
--
-- zone_demand_calibration and od_pair_calibration hold weights derived from
-- a real month of TLC High-Volume For-Hire Vehicle records, but neither
-- records WHICH month. Two bootstraps calibrated from different months are
-- indistinguishable in the data, which makes "is the generated demand
-- still close to the real surface" unanswerable the moment the calibration
-- month changes. See docs/schema-review.md F12.
--
-- source_month is nullable: a calibration restored from before this
-- migration genuinely does not know its own month, and inventing one would
-- be worse than admitting it. zone-demand-prepare fills it from here on.

ALTER TABLE zone_demand_calibration ADD COLUMN IF NOT EXISTS source_month date;
ALTER TABLE zone_demand_calibration ADD COLUMN IF NOT EXISTS calibrated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE od_pair_calibration ADD COLUMN IF NOT EXISTS source_month date;
ALTER TABLE od_pair_calibration ADD COLUMN IF NOT EXISTS calibrated_at timestamptz NOT NULL DEFAULT now();

-- The first of a month, always - a calibration is built from a whole
-- month's file, never a partial one. Written as a day-of-month test
-- rather than date_trunc: a CHECK expression has to be immutable, and
-- date_trunc has a timestamptz overload that is only stable.
ALTER TABLE zone_demand_calibration DROP CONSTRAINT IF EXISTS zone_demand_calibration_source_month_check;
ALTER TABLE zone_demand_calibration ADD CONSTRAINT zone_demand_calibration_source_month_check
    CHECK (source_month IS NULL OR EXTRACT(day FROM source_month) = 1);

ALTER TABLE od_pair_calibration DROP CONSTRAINT IF EXISTS od_pair_calibration_source_month_check;
ALTER TABLE od_pair_calibration ADD CONSTRAINT od_pair_calibration_source_month_check
    CHECK (source_month IS NULL OR EXTRACT(day FROM source_month) = 1);
