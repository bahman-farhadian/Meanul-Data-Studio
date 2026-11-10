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
attempt. This container only reads the already-local file in fixed-size batches
and aggregates each batch before reading the next. Nothing ever holds the
whole month: a month is ~20.4 million trips, and the first version of
this script collected every row group into a list and then pd.concat'd
it, so the list and the concatenated frame were both resident at once
and the filter below made a third copy. That was OOM-killed inside a
3 GB container (exit 137) - the cap doing its job, but the wrong thing to
need a 3 GB cap for.

Every number here is summable, which is why batching works at all:
counts add, and a mean is a sum and a count that add. The two running
aggregates are bounded by the zone grid, not by the month - at most
263 x 24 x 7 cells for zone demand and 263 x 263 for OD pairs.

Two details are load-bearing and were both measured, not assumed:

- Batches are a fixed row count, NOT one Parquet row group. Aggregating
  per row group still peaked at 2.1 GB on this file (measured: anon
  2134 MiB, page cache 13 MiB - so real allocation, not file cache),
  because a row group here is ~1M trips and the churn of building and
  freeing a frame that size 20 times over does not come back to the OS.
  Row-group size is also a property of whoever wrote the file: a month
  published as four 5M-row groups would have been far worse. A row count
  we choose is the only bound that holds for any file.
- MALLOC_ARENA_MAX is set in the compose file, and the pyarrow pool is
  released after each batch. Without them the high-water is the sum of
  what every allocator arena ever held, not what is live at once.

Measured on 2025-01 (20,405,666 trips, 469 MB Parquet): 823 MiB peak,
against 2092 MiB for the per-row-group version and over 3072 MiB for the
whole-month version, which the 3 GB cap killed. The batched result is
identical to the row-group result except for avg_fare, which differs by
at most 4e-16 relative - one unit in the last place, from summing the
same numbers in a different order.

Only the two derived aggregates (a few thousand rows each) are written
here, as plain CSV in /data, restored into
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
import pyarrow as pa
import pyarrow.parquet as pq

COLUMNS = [
    "PULocationID", "DOLocationID", "pickup_datetime",
    "trip_miles", "trip_time", "base_passenger_fare", "driver_pay",
]

# Rows per batch, not bytes: seven columns of fixed-width numerics, so rows
# and bytes are proportional here. 250k keeps one batch near 15 MB.
BATCH_ROWS = int(os.environ.get("ZONE_DEMAND_BATCH_ROWS", "250000"))


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

    log(f"reading {input_path} (column-projected, {BATCH_ROWS:,}-row batches)")
    t0 = time.monotonic()
    pf = pq.ParquetFile(input_path)
    pool = pa.default_memory_pool()

    zone_parts: list[pd.DataFrame] = []
    od_parts: list[pd.DataFrame] = []
    read_rows = 0
    kept_rows = 0
    fare_total = 0.0
    pay_total = 0.0

    for n, batch in enumerate(pf.iter_batches(batch_size=BATCH_ROWS, columns=COLUMNS), 1):
        part = batch.to_pandas()
        del batch
        read_rows += len(part)

        # TLC's real LocationID range used in trip records includes 264
        # ("Unknown") and 265 ("N/A") - confirmed directly against TLC's own
        # taxi_zone_lookup.csv - which are not real geographic zones and do
        # not exist in city_zones. Filtered here, once, with the drop rate
        # logged, rather than letting them reach bootstrap as a foreign-key
        # violation (ON CONFLICT does not catch that - it only suppresses a
        # primary-key/unique conflict, a different thing).
        #
        # 0 = Monday .. 6 = Sunday, pandas' own convention - documented in
        # 012_zone_demand_calibration.sql and relied on by hailpath_common.
        # demand_calibration, which passes date.weekday() straight through.
        part = part[
            part["PULocationID"].between(1, 263)
            & part["DOLocationID"].between(1, 263)
        ].assign(
            hour_of_day=lambda d: d["pickup_datetime"].dt.hour,
            day_of_week=lambda d: d["pickup_datetime"].dt.dayofweek,
        )
        kept_rows += len(part)
        fare_total += part["base_passenger_fare"].sum()
        pay_total += part["driver_pay"].sum()

        zone_parts.append(
            part.groupby(["PULocationID", "hour_of_day", "day_of_week"])
            .size()
            .reset_index(name="trips")
        )
        # Sums and non-null counts rather than means, because a mean of
        # means is not the mean. pandas' own mean skips nulls, so the
        # count has to skip them the same way for the division at the end
        # to agree with what a single-pass groupby would have produced.
        od_parts.append(
            part.groupby(["PULocationID", "DOLocationID"])
            .agg(
                trips=("trip_miles", "size"),
                fare_sum=("base_passenger_fare", "sum"),
                fare_n=("base_passenger_fare", "count"),
                time_sum=("trip_time", "sum"),
                time_n=("trip_time", "count"),
            )
            .reset_index()
        )
        del part
        # Hand the arrow buffers back rather than leaving them in the pool
        # to be reused later - the pool's high-water is what the cgroup
        # sees, and the cgroup is what kills this container.
        pool.release_unused()
        if n % 10 == 0:
            log(f"  {read_rows:,} trips ({time.monotonic() - t0:.0f}s elapsed)")

    log(f"read {read_rows:,} real trips in {time.monotonic() - t0:.0f}s")
    dropped = read_rows - kept_rows
    log(f"dropped {dropped:,} trips with a non-geographic pickup/dropoff id (264/265) - {dropped / read_rows:.2%}")

    # Trip counts per (zone, hour, day), normalized to the overall average
    # cell so weight=1.0 means "an ordinary hour for an ordinary zone" -
    # the same semantic the code this replaces already used, so nothing
    # downstream needs to change how it interprets the number.
    zone_demand = (
        pd.concat(zone_parts, ignore_index=True)
        .groupby(["PULocationID", "hour_of_day", "day_of_week"], as_index=False)["trips"]
        .sum()
    )
    del zone_parts
    zone_demand["weight"] = zone_demand["trips"] / zone_demand["trips"].mean()
    zone_demand = zone_demand.rename(columns={"PULocationID": "zone_id"})
    # The month rides in the CSV rather than being read from the
    # environment at load time. A calibration file already on disk cannot
    # then be mislabelled by someone changing TLC_TRIP_DATA_MONTH between
    # the prepare step and the bootstrap - which is precisely the
    # confusion source_month exists to prevent. Written as the first of
    # the month because that is what the CHECK constraint requires.
    zone_demand["source_month"] = f"{month}-01"
    zone_demand[["zone_id", "hour_of_day", "day_of_week", "weight", "source_month"]].to_csv(
        zone_demand_out, index=False
    )
    log(f"zone_demand_calibration: {len(zone_demand):,} rows")

    # Real OD shares, normalized within each pickup zone (sums to ~1.0
    # per pickup_zone_id) so it composes with however many trips a zone
    # actually generates rather than carrying its own absolute scale.
    od_counts = (
        pd.concat(od_parts, ignore_index=True)
        .groupby(["PULocationID", "DOLocationID"], as_index=False)
        .sum()
    )
    del od_parts
    od_counts["avg_fare"] = od_counts["fare_sum"] / od_counts["fare_n"]
    od_counts["avg_duration_s"] = od_counts["time_sum"] / od_counts["time_n"]
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
    od_counts["source_month"] = f"{month}-01"
    od_counts[["pickup_zone_id", "dropoff_zone_id", "trip_share", "avg_fare",
               "avg_duration_s", "source_month"]].to_csv(
        od_pair_out, index=False
    )
    log(f"od_pair_calibration: {len(od_counts):,} rows")

    # A free check, printed for a human to read - not written anywhere:
    # how the real driver-pay-to-fare ratio this month compares to
    # PLATFORM_COMMISSION_PCT's own default.
    real_commission = 1 - (pay_total / fare_total)
    log(f"real commission ratio this month: {real_commission:.3f} - compare against PLATFORM_COMMISSION_PCT")

    return 0


if __name__ == "__main__":
    sys.exit(main())
