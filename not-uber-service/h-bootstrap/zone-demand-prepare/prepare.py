"""Turn one real month of NYC TLC's High-Volume For-Hire Vehicle trip
records into the two small calibration tables zone-demand-prepare exists
to build.

Runs once, in this throwaway container - never against the real stack.
The Parquet file itself (a few hundred MB) is downloaded once, on the
host, by `make tlc-trips-fetch` (part of `make prepare`) - not by this
script. Real, billed traffic on a metered host is worth paying exactly
once, not on every retry: streaming it fresh from CloudFront on every run
(the original design here) meant a run that failed partway - for any
reason, not just a network one - re-paid the whole download on its next
attempt. This container only reads the already-local file, still with
column projection so the full six-column-wide frame is the only thing
ever held in memory. Only the two derived aggregates (a few thousand
rows each) are written here, as plain CSV in /data, restored into
zone_demand_calibration/od_pair_calibration by
h-bootstrap/bootstrap/demand.py during a real bootstrap run.

TLC_TRIP_DATA_MONTH is pinned, not "the latest available", on purpose:
the same month always produces the same calibration, the same reason
this whole project pins a RANDOM_SEED everywhere else.
"""

import os
import sys
import time

import pandas as pd
import pyarrow.parquet as pq

COLUMNS = [
    "PULocationID", "DOLocationID", "pickup_datetime",
    "trip_miles", "trip_time", "base_passenger_fare", "driver_pay",
]


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    month = os.environ.get("TLC_TRIP_DATA_MONTH", "2025-01")
    out_dir = os.environ.get("OUTPUT_DIR", "/data")
    input_path = os.path.join(out_dir, f"tlc-trips-{month}.parquet")

    zone_demand_out = os.path.join(out_dir, "zone_demand_calibration.csv")
    od_pair_out = os.path.join(out_dir, "od_pair_calibration.csv")

    if os.path.exists(zone_demand_out) and os.path.exists(od_pair_out):
        log(f"already built: {zone_demand_out}, {od_pair_out}")
        return 0

    if not os.path.exists(input_path):
        log(f"{input_path} not found - run `make tlc-trips-fetch` first (part of `make prepare`)")
        return 1

    log(f"reading {input_path} (column-projected)")
    t0 = time.monotonic()
    frames = []
    pf = pq.ParquetFile(input_path)
    total_groups = pf.num_row_groups
    for i in range(total_groups):
        frames.append(pf.read_row_group(i, columns=COLUMNS).to_pandas())
        log(f"  row group {i + 1}/{total_groups} ({time.monotonic() - t0:.0f}s elapsed)")

    df = pd.concat(frames, ignore_index=True)
    del frames
    log(f"loaded {len(df):,} real trips in {time.monotonic() - t0:.0f}s")

    # TLC's real LocationID range used in trip records includes 264
    # ("Unknown") and 265 ("N/A") - confirmed directly against TLC's own
    # taxi_zone_lookup.csv - which are not real geographic zones and do
    # not exist in city_zones. Filtered here, once, with the drop rate
    # logged, rather than letting them reach bootstrap as a foreign-key
    # violation (ON CONFLICT does not catch that - it only suppresses a
    # primary-key/unique conflict, a different thing).
    before = len(df)
    df = df[df["PULocationID"].between(1, 263) & df["DOLocationID"].between(1, 263)]
    dropped = before - len(df)
    log(f"dropped {dropped:,} trips with a non-geographic pickup/dropoff id (264/265) - {dropped / before:.2%}")

    df["hour_of_day"] = df["pickup_datetime"].dt.hour
    # 0 = Monday .. 6 = Sunday, pandas' own convention - documented in
    # 012_zone_demand_calibration.sql and relied on by nus_common.
    # demand_calibration, which passes date.weekday() straight through.
    df["day_of_week"] = df["pickup_datetime"].dt.dayofweek

    # Trip counts per (zone, hour, day), normalized to the overall average
    # cell so weight=1.0 means "an ordinary hour for an ordinary zone" -
    # the same semantic the code this replaces already used, so nothing
    # downstream needs to change how it interprets the number.
    zone_demand = (
        df.groupby(["PULocationID", "hour_of_day", "day_of_week"])
        .size()
        .reset_index(name="trips")
    )
    zone_demand["weight"] = zone_demand["trips"] / zone_demand["trips"].mean()
    zone_demand = zone_demand.rename(columns={"PULocationID": "zone_id"})
    zone_demand[["zone_id", "hour_of_day", "day_of_week", "weight"]].to_csv(
        zone_demand_out, index=False
    )
    log(f"zone_demand_calibration: {len(zone_demand):,} rows")

    # Real OD shares, normalized within each pickup zone (sums to ~1.0
    # per pickup_zone_id) so it composes with however many trips a zone
    # actually generates rather than carrying its own absolute scale.
    od_counts = (
        df.groupby(["PULocationID", "DOLocationID"])
        .agg(trips=("trip_miles", "size"),
             avg_fare=("base_passenger_fare", "mean"),
             avg_duration_s=("trip_time", "mean"))
        .reset_index()
    )
    pickup_totals = od_counts.groupby("PULocationID")["trips"].transform("sum")
    od_counts["trip_share"] = od_counts["trips"] / pickup_totals
    # od_pair_calibration.avg_duration_s is integer (whole seconds is all
    # the precision a duration estimate needs) but a mean is not naturally
    # whole - round it here rather than writing "218.4" for Postgres to
    # reject.
    od_counts["avg_duration_s"] = od_counts["avg_duration_s"].round().astype("Int64")
    od_counts = od_counts.rename(
        columns={"PULocationID": "pickup_zone_id", "DOLocationID": "dropoff_zone_id"}
    )
    od_counts[["pickup_zone_id", "dropoff_zone_id", "trip_share", "avg_fare", "avg_duration_s"]].to_csv(
        od_pair_out, index=False
    )
    log(f"od_pair_calibration: {len(od_counts):,} rows")

    # A free check, printed for a human to read - not written anywhere:
    # how the real driver-pay-to-fare ratio this month compares to
    # PLATFORM_COMMISSION_PCT's own default.
    real_commission = 1 - (df["driver_pay"].sum() / df["base_passenger_fare"].sum())
    log(f"real commission ratio this month: {real_commission:.3f} - compare against PLATFORM_COMMISSION_PCT")

    return 0


if __name__ == "__main__":
    sys.exit(main())
