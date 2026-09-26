#!/usr/bin/env python3
"""Write the Superset assets this piece imports.

The same arrangement f-infra-grafana already uses: this script is the source
and assets/ is what it produces, so the SQL and the layout live in one file
instead of being spread across twenty YAML documents whose cross-references
have to be kept in step by hand. check-assets.py validates what was written,
not this module.

Superset keeps charts and dashboards in its own database, so "provisioned"
means imported. The import is declarative and re-runnable:

    superset import-directory /app/assets --overwrite

A chart edited in the browser is NOT written back here. Export it and commit
the change, exactly the contract the Grafana dashboards have.

IDENTIFIERS ARE DERIVED, NEVER INVENTED. Every uuid comes from uuid5 over
the same namespace init/register_database.py uses, so the value written into
a dataset file and the value that script writes into the connection cannot
drift, and a second import updates what the first one made rather than
creating a parallel copy of it.

METRICS LIVE ON THE DATASET, WHICH IS THE WHOLE POINT. The tables underneath
are SummingMergeTree, filled per node: the same hour and zone exists on both
shards as two partial rows. Anything that divides has to sum first. Defining
those metrics here means a chart is built by choosing "Fulfilment rate" from
a list, and there is no path through the UI that produces an average of
averages instead.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "assets"

# The same namespace init/register_database.py pins, so the ClickHouse
# connection this bundle references is the one that script registers.
NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/bahman-farhadian/Meanul-Data-Studio"
)
DATABASE_NAME = "ClickHouse (nus)"
DATABASE_UUID = uuid.uuid5(NAMESPACE, "database/clickhouse")
# The subdirectory name under datasets/ is Superset's own convention; it has
# no meaning beyond grouping, since the link is database_uuid.
DATABASE_SLUG = "clickhouse_nus"

VERSION = "1.0.0"


def ident(kind: str, name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}/{name}")


# --------------------------------------------------------------------------
# YAML, written directly
#
# PyYAML is not a dependency of this repository and there is no reason to add
# one to emit documents this regular. Everything here is a string, a number,
# a bool, null, a flat list of mappings, or a JSON blob - so the writer only
# has to cover those, and it quotes every string rather than deciding when a
# value needs it.
# --------------------------------------------------------------------------

def _scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return "'" + str(value).replace("'", "''") + "'"


def to_yaml(document: dict, indent: int = 0) -> str:
    pad = " " * indent
    lines = []
    for key, value in document.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            lines.append(f"{pad}{key}:")
            for item in value:
                body = to_yaml(item, indent + 4).splitlines()
                lines.append(f"{pad}  - {body[0].strip()}")
                lines.extend(body[1:])
        elif isinstance(value, list) and not value:
            lines.append(f"{pad}{key}: []")
        else:
            lines.append(f"{pad}{key}: {_scalar(value)}")
    return "\n".join(lines) + "\n"


def column(name: str, ctype: str, *, dttm: bool = False, groupby: bool = True,
           filterable: bool = True, description: str | None = None) -> dict:
    return {
        "column_name": name,
        "verbose_name": None,
        "type": ctype,
        "is_dttm": dttm,
        "is_active": True,
        "groupby": groupby,
        "filterable": filterable,
        "expression": None,
        "description": description,
        "python_date_format": None,
        "extra": None,
        "advanced_data_type": None,
    }


def metric(name: str, verbose: str, expression: str, *, fmt: str,
           kind: str = "sum", description: str | None = None) -> dict:
    return {
        "metric_name": name,
        "verbose_name": verbose,
        "metric_type": kind,
        "expression": expression,
        "description": description,
        "d3format": fmt,
        "currency": None,
        "warning_text": None,
        "extra": None,
    }


def dataset(name: str, *, description: str, main_dttm: str,
            columns: list[dict], metrics: list[dict], comment: str) -> dict:
    return {
        "_path": f"datasets/{DATABASE_SLUG}/{name}.yaml",
        "_comment": comment,
        "table_name": name,
        "schema": "nus",
        "uuid": str(ident("dataset", name)),
        "database_uuid": str(DATABASE_UUID),
        "version": VERSION,
        "main_dttm_col": main_dttm,
        "description": description,
        "default_endpoint": None,
        "offset": 0,
        "cache_timeout": None,
        "catalog": None,
        # Empty, deliberately: these are PHYSICAL datasets. A virtual one
        # carries its own SQL, which Superset transpiles towards the target
        # dialect on import - and ClickHouse's aggregate-state functions are
        # not something a generic transpiler should be asked to rewrite.
        "sql": "",
        "params": None,
        "template_params": None,
        "filter_select_enabled": True,
        "fetch_values_predicate": None,
        "extra": None,
        "normalize_columns": False,
        "always_filter_main_dttm": False,
        "columns": columns,
        "metrics": metrics,
    }


def chart(slug: str, *, name: str, viz: str, dataset_name: str,
          params: dict, description: str) -> dict:
    params = dict(params)
    params["viz_type"] = viz
    # Superset rewrites this on import to the real dataset id; it is carried
    # only because the chart's own params are where the frontend looks.
    params.setdefault("datasource", "1__table")
    return {
        "_path": f"charts/{slug}.yaml",
        "_comment": None,
        "slice_name": name,
        "description": description,
        "uuid": str(ident("chart", slug)),
        "dataset_uuid": str(ident("dataset", dataset_name)),
        "version": VERSION,
        "viz_type": viz,
        "cache_timeout": None,
        "certified_by": None,
        "certification_details": None,
        "query_context": None,
        "params": params,
    }


# --------------------------------------------------------------------------
# Dashboard layout
#
# Superset stores a dashboard as a tree of typed nodes keyed by id. Writing
# that by hand is where a hand-maintained bundle goes wrong: a chart listed
# in position but absent from charts/ imports a dashboard with a hole in it,
# and nothing complains. Built from rows here instead, so the two cannot
# disagree.
# --------------------------------------------------------------------------

def layout(title: str, rows: list) -> dict:
    position: dict = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
    }
    grid_children: list[str] = []
    chart_index = 0
    markdown_index = 0

    for index, row in enumerate(rows, start=1):
        row_id = f"ROW-{index}"
        children: list[str] = []
        if isinstance(row, str):
            markdown_index += 1
            node_id = f"MARKDOWN-{markdown_index}"
            position[node_id] = {
                "type": "MARKDOWN",
                "id": node_id,
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "meta": {"width": 12, "height": 8, "code": row},
            }
            children.append(node_id)
        else:
            assert sum(width for _, width, _ in row) == 12, (row_id, row)
            for slug, width, height in row:
                chart_index += 1
                node_id = f"CHART-{chart_index}"
                position[node_id] = {
                    "type": "CHART",
                    "id": node_id,
                    "children": [],
                    "parents": ["ROOT_ID", "GRID_ID", row_id],
                    "meta": {
                        "uuid": str(ident("chart", slug)),
                        "width": width,
                        "height": height,
                        "chartId": chart_index,
                        "sliceName": slug,
                    },
                }
                children.append(node_id)
        position[row_id] = {
            "type": "ROW",
            "id": row_id,
            "children": children,
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        grid_children.append(row_id)

    position["GRID_ID"] = {
        "type": "GRID",
        "id": "GRID_ID",
        "children": grid_children,
        "parents": ["ROOT_ID"],
    }
    return position


def dashboard(slug: str, *, title: str, description: str, rows: list,
              colours: dict[str, str], comment: str) -> dict:
    return {
        "_path": f"dashboards/{slug}.yaml",
        "_comment": comment,
        "dashboard_title": title,
        "description": description,
        "uuid": str(ident("dashboard", slug)),
        "version": VERSION,
        "published": True,
        "slug": slug,
        "css": "",
        "certified_by": None,
        "certification_details": None,
        "metadata": {
            "color_scheme": "",
            "refresh_frequency": 0,
            "expanded_slices": {},
            "timed_refresh_immune_slices": [],
            "default_filters": "{}",
            "cross_filters_enabled": True,
            # Colour follows the outcome, not its rank in the result, and
            # matches the Grafana dashboards so the same four endings are the
            # same four colours in both tools.
            "label_colors": colours,
        },
        "position": layout(title, rows),
    }


# ==========================================================================
# Datasets - the rollups, and the metrics that keep a chart honest
# ==========================================================================

PCT = ",.1%"
NUM = ",d"
MONEY = "$,.0f"
SECONDS = ",.0f"

ROLLUP_RULE = (
    "SummingMergeTree, filled per node: the same key exists on both shards "
    "as two partial rows. Every ratio below sums first and divides once."
)

# A metric may never be named after a column it reads. ClickHouse prefers a
# SELECT alias over a table column, so `sum(revenue) AS revenue` turns the
# NEXT metric's `sum(revenue)` into sum of an aggregate - ILLEGAL_AGGREGATION,
# code 184, and only when two such metrics are selected together, which is
# exactly what a dashboard does. Measured against a real server, not guessed:
# six of these were in the first draft and three datasets would not query at
# all. check-assets.py now refuses the collision.

FULFILMENT = dataset(
    "fulfilment_hourly",
    description="Every request that reached an ending, per hour and pickup zone.",
    main_dttm="hour",
    comment=(
        "The funnel that finally counts the trips that did NOT complete.\n"
        "Every other rollup in this warehouse filters status = 'completed',\n"
        "so cancellations and unmatched requests appeared in no aggregate at\n"
        "all until this table existed. Bucketed on when the ride was ASKED\n"
        "for, not when it ended - a fulfilment rate is about the demand that\n"
        "arrived in an hour.\n"
        + ROLLUP_RULE
    ),
    columns=[
        column("hour", "DATETIME", dttm=True),
        column("pickup_zone_id", "STRING", description="TLC LocationID of the pickup."),
        column("trips_ended", "BIGINT", groupby=False),
        column("completed", "BIGINT", groupby=False),
        column("cancelled_by_passenger", "BIGINT", groupby=False),
        column("cancelled_by_driver", "BIGINT", groupby=False),
        column("no_driver_found", "BIGINT", groupby=False),
        column("cancelled_after_arrival", "BIGINT", groupby=False),
        column("match_s_sum", "BIGINT", groupby=False, filterable=False),
        column("matched_trips", "BIGINT", groupby=False, filterable=False),
        column("wait_s_sum", "BIGINT", groupby=False, filterable=False),
        column("waited_trips", "BIGINT", groupby=False, filterable=False),
    ],
    metrics=[
        metric("requests", "Requests", "sum(trips_ended)", fmt=NUM, kind="count",
               description="Every request that reached an ending, served or not."),
        metric("completed_trips", "Completed", "sum(completed)", fmt=NUM, kind="count"),
        metric("unmatched", "No driver found", "sum(no_driver_found)",
               fmt=NUM, kind="count",
               description="Nobody could be offered the ride at all. A supply failure, not a cancellation."),
        metric("rider_cancels", "Cancelled by rider",
               "sum(cancelled_by_passenger)", fmt=NUM, kind="count"),
        metric("driver_cancels", "Cancelled by driver",
               "sum(cancelled_by_driver)", fmt=NUM, kind="count"),
        metric("fulfilment_rate", "Fulfilment rate",
               "sum(completed) / greatest(sum(trips_ended), 1)", fmt=PCT, kind="avg",
               description="Completed out of everything asked for. The one number that counts the requests nobody served."),
        metric("cancellation_rate", "Cancellation rate",
               "(sum(cancelled_by_passenger) + sum(cancelled_by_driver)) "
               "/ greatest(sum(trips_ended), 1)", fmt=PCT, kind="avg",
               description="Both sides together. A trip nobody could be found for is counted separately."),
        metric("no_driver_rate", "Unmatched rate",
               "sum(no_driver_found) / greatest(sum(trips_ended), 1)", fmt=PCT, kind="avg"),
        metric("post_arrival_cancel_rate", "Cancelled after the driver arrived",
               "sum(cancelled_after_arrival) / greatest(sum(cancelled_by_passenger) "
               "+ sum(cancelled_by_driver), 1)", fmt=PCT, kind="avg",
               description="The line a real platform draws to charge a cancellation fee."),
        metric("time_to_match_s", "Time to match",
               "sum(match_s_sum) / greatest(sum(matched_trips), 1)", fmt=SECONDS,
               kind="avg", description="Request to a driver being picked, in seconds."),
        metric("rider_wait_s", "Rider wait at the kerb",
               "sum(wait_s_sum) / greatest(sum(waited_trips), 1)", fmt=SECONDS,
               kind="avg",
               description="Driver arriving to the rider getting in. What the 'arrived' state exists to measure."),
    ],
)

FUNNEL = dataset(
    "dispatch_funnel_hourly",
    description="Every offer dispatch made, and what the driver said, per hour and zone.",
    main_dttm="hour",
    comment=(
        "The matching funnel. Dispatch offers a ride to one candidate at a\n"
        "time with a deadline, so acceptance rate, offers per match and\n"
        "time-to-match come from here and from nowhere else - none of them\n"
        "existed while dispatch simply assigned a driver.\n"
        "'expired' and 'declined' are kept apart on purpose: a driver who\n"
        "said no is a different supply signal from a phone left face-down.\n"
        + ROLLUP_RULE
    ),
    columns=[
        column("hour", "DATETIME", dttm=True),
        column("pickup_zone_id", "STRING"),
        column("offers_made", "BIGINT", groupby=False),
        column("offers_accepted", "BIGINT", groupby=False),
        column("offers_declined", "BIGINT", groupby=False),
        column("offers_expired", "BIGINT", groupby=False),
        column("offers_cancelled", "BIGINT", groupby=False),
        column("eta_seconds_sum", "BIGINT", groupby=False, filterable=False),
        column("eta_offers", "BIGINT", groupby=False, filterable=False),
        column("accepted_eta_sum", "BIGINT", groupby=False, filterable=False),
        column("response_s_sum", "BIGINT", groupby=False, filterable=False),
        column("responses", "BIGINT", groupby=False, filterable=False),
    ],
    metrics=[
        metric("offers", "Offers made", "sum(offers_made)", fmt=NUM, kind="count"),
        metric("accepted", "Offers accepted", "sum(offers_accepted)", fmt=NUM, kind="count"),
        metric("declined", "Declined", "sum(offers_declined)", fmt=NUM, kind="count"),
        metric("expired", "Expired unanswered", "sum(offers_expired)", fmt=NUM, kind="count"),
        metric("acceptance_rate", "Acceptance rate",
               "sum(offers_accepted) / greatest(sum(offers_made), 1)", fmt=PCT, kind="avg"),
        metric("offers_per_match", "Offers per match",
               "sum(offers_made) / greatest(sum(offers_accepted), 1)", fmt=",.2f",
               kind="avg",
               description="How many drivers were asked before one took the ride. Rises before fulfilment falls."),
        metric("eta_offered_s", "Pickup ETA offered",
               "sum(eta_seconds_sum) / greatest(sum(eta_offers), 1)", fmt=SECONDS, kind="avg"),
        metric("eta_accepted_s", "Pickup ETA accepted",
               "sum(accepted_eta_sum) / greatest(sum(offers_accepted), 1)", fmt=SECONDS,
               kind="avg",
               description="Read the gap, not the level: offers go out nearest-first, so the accepting driver is among the furthest asked."),
        metric("response_s", "Time to answer",
               "sum(response_s_sum) / greatest(sum(responses), 1)", fmt=SECONDS, kind="avg"),
    ],
)

TRIPS_DAILY = dataset(
    "trip_stats_daily",
    description="Completed trips, revenue, driver pay, surge and overruns per day and pickup zone.",
    main_dttm="day",
    comment=(
        "Money, at day grain. revenue and payout_total are Decimal64(2) and\n"
        "summed exactly, because float addition is not associative and this\n"
        "column is summed inside background merges in an order nobody\n"
        "controls. Take rate below casts to float on the RATIO and never on\n"
        "the money.\n"
        + ROLLUP_RULE
    ),
    columns=[
        column("day", "DATE", dttm=True),
        column("pickup_zone_id", "STRING"),
        column("completed_trips", "BIGINT", groupby=False),
        column("revenue", "DECIMAL", groupby=False),
        column("payout_total", "DECIMAL", groupby=False),
        column("surge_sum", "DOUBLE", groupby=False, filterable=False),
        column("route_km_total", "DOUBLE", groupby=False, filterable=False),
        column("overrun_trips", "BIGINT", groupby=False),
    ],
    metrics=[
        metric("trips", "Completed trips", "sum(completed_trips)", fmt=NUM, kind="count"),
        metric("gross_revenue", "Revenue", "sum(revenue)", fmt=MONEY),
        metric("payout", "Driver pay", "sum(payout_total)", fmt=MONEY),
        metric("platform_fee", "Platform fee", "sum(revenue) - sum(payout_total)", fmt=MONEY),
        metric("take_rate", "Take rate",
               "toFloat64(sum(revenue) - sum(payout_total)) "
               "/ greatest(toFloat64(sum(revenue)), 1)", fmt=PCT, kind="avg",
               description="What the platform keeps. Decimal divided by Decimal truncates to the left operand's scale, so the cast is on the ratio."),
        metric("avg_surge", "Average surge",
               "sum(surge_sum) / greatest(sum(completed_trips), 1)", fmt=",.2f", kind="avg",
               description="Surge added up and divided by trips - never an average of averages."),
        metric("overrun_rate", "Slower than predicted",
               "sum(overrun_trips) / greatest(sum(completed_trips), 1)", fmt=PCT, kind="avg"),
        metric("km_total", "Distance driven", "sum(route_km_total)", fmt=",.0f"),
    ],
)

OD = dataset(
    "od_matrix_daily",
    description="Completed trips and revenue for every pickup-to-dropoff pair, per day.",
    main_dttm="day",
    comment=(
        "Where the city actually travels. Directly comparable against the\n"
        "real TLC od_pair_calibration the demand generator is built from,\n"
        "which makes this a standing check that the simulation still looks\n"
        "like the month it was calibrated on.\n"
        + ROLLUP_RULE
    ),
    columns=[
        column("day", "DATE", dttm=True),
        column("pickup_zone_id", "STRING"),
        column("dropoff_zone_id", "STRING"),
        column("completed_trips", "BIGINT", groupby=False),
        column("revenue", "DECIMAL", groupby=False),
        column("route_km_total", "DOUBLE", groupby=False, filterable=False),
    ],
    metrics=[
        metric("trips", "Completed trips", "sum(completed_trips)", fmt=NUM, kind="count"),
        metric("gross_revenue", "Revenue", "sum(revenue)", fmt=MONEY),
        metric("km_total", "Distance driven", "sum(route_km_total)", fmt=",.0f"),
    ],
)

UTILIZATION = dataset(
    "driver_utilization_hourly",
    description="Ticks each driver spent online, and how many of those were on a trip.",
    main_dttm="hour",
    comment=(
        "One row per driver per hour, counted from position reports rather\n"
        "than from trips - so a driver who was online and never matched is\n"
        "in the denominator, which is the only way this number means\n"
        "anything.\n"
        + ROLLUP_RULE
    ),
    columns=[
        column("hour", "DATETIME", dttm=True),
        column("driver_id", "STRING"),
        column("online_ticks", "BIGINT", groupby=False, filterable=False),
        column("busy_ticks", "BIGINT", groupby=False, filterable=False),
    ],
    metrics=[
        metric("utilization", "Utilization",
               "sum(busy_ticks) / greatest(sum(online_ticks), 1)", fmt=PCT, kind="avg",
               description="Share of online time spent on a trip."),
        metric("online_time", "Online ticks", "sum(online_ticks)", fmt=NUM),
        metric("drivers", "Drivers seen", "count(distinct driver_id)", fmt=NUM, kind="count_distinct"),
    ],
)

DURATIONS = dataset(
    "trip_duration_percentiles_hourly",
    description="Trip duration percentiles per hour and pickup zone.",
    main_dttm="hour",
    comment=(
        "AggregatingMergeTree, not SummingMergeTree. The two state columns\n"
        "hold quantile sketches, not numbers - they are readable only\n"
        "through quantileMerge, and adding two of them together is\n"
        "meaningless. They are therefore marked ungroupable and\n"
        "unfilterable, so the only way to reach them is the metrics below."
    ),
    columns=[
        column("hour", "DATETIME", dttm=True),
        column("pickup_zone_id", "STRING"),
        column("p50_state", "OTHER", groupby=False, filterable=False,
               description="An aggregate state. Read with quantileMerge, never directly."),
        column("p95_state", "OTHER", groupby=False, filterable=False,
               description="An aggregate state. Read with quantileMerge, never directly."),
    ],
    metrics=[
        metric("p50_s", "Median trip duration", "quantileMerge(0.5)(p50_state)",
               fmt=SECONDS, kind="avg"),
        metric("p95_s", "p95 trip duration", "quantileMerge(0.95)(p95_state)",
               fmt=SECONDS, kind="avg",
               description="The long tail riders actually complain about."),
    ],
)

DATASETS = [FULFILMENT, FUNNEL, TRIPS_DAILY, OD, UTILIZATION, DURATIONS]


# ==========================================================================
# Charts
#
# One job per chart. Two measures of different scale are two charts, never
# two y-axes - the same rule the Grafana tier follows, for the same reason:
# the smaller series becomes a flat line along the bottom and a second axis
# only moves the lie somewhere harder to see.
# ==========================================================================

NO_FILTER = "No filter"


def big_number(slug, name, dataset_name, metric_name, fmt, description):
    return chart(
        slug, name=name, viz="big_number_total", dataset_name=dataset_name,
        description=description,
        params={
            "metric": metric_name,
            "adhoc_filters": [],
            "time_range": NO_FILTER,
            "y_axis_format": fmt,
            "header_font_size": 0.4,
            "subheader_font_size": 0.125,
            "subheader": description,
        },
    )


def timeseries(slug, name, dataset_name, x_axis, metrics, description, *,
               fmt=",.2f", grain="P1D", series="line", stacked=False,
               legend=True, groupby=None):
    params = {
        "x_axis": x_axis,
        "time_grain_sqla": grain,
        "metrics": metrics,
        "groupby": groupby or [],
        "adhoc_filters": [],
        "row_limit": 10000,
        "time_range": NO_FILTER,
        "x_axis_sort_asc": True,
        "y_axis_format": fmt,
        "seriesType": series,
        "markerEnabled": False,
        "show_legend": legend,
        "rich_tooltip": True,
        "tooltipSortByMetric": True,
    }
    if stacked:
        params["stack"] = "Stack"
        params["opacity"] = 0.9
    viz = "echarts_timeseries_bar" if series == "bar" else "echarts_timeseries_line"
    return chart(slug, name=name, viz=viz, dataset_name=dataset_name,
                 params=params, description=description)


def table(slug, name, dataset_name, groupby, metrics, description, *,
          order_desc=True, row_limit=25):
    return chart(
        slug, name=name, viz="table", dataset_name=dataset_name,
        description=description,
        params={
            "query_mode": "aggregate",
            "groupby": groupby,
            "metrics": metrics,
            "adhoc_filters": [],
            "row_limit": row_limit,
            "order_desc": order_desc,
            "time_range": NO_FILTER,
            "include_search": True,
            "show_cell_bars": True,
            "color_pn": False,
        },
    )


CHARTS = [
    big_number("fulfilment-rate", "Fulfilment rate", "fulfilment_hourly",
               "fulfilment_rate", PCT,
               "Completed out of every request that reached an ending."),
    big_number("cancellation-rate", "Cancellation rate", "fulfilment_hourly",
               "cancellation_rate", PCT,
               "Rider and driver cancellations together, out of all requests."),
    big_number("acceptance-rate", "Driver acceptance rate", "dispatch_funnel_hourly",
               "acceptance_rate", PCT,
               "Offers accepted out of offers made."),
    big_number("take-rate", "Take rate", "trip_stats_daily", "take_rate", PCT,
               "What the platform keeps of every fare."),
    big_number("trips-total", "Completed trips", "trip_stats_daily", "trips", NUM,
               "Trips that finished and were charged for."),
    big_number("revenue-total", "Revenue", "trip_stats_daily", "gross_revenue", MONEY,
               "Final fares across the period, surge included."),

    timeseries("fulfilment-trend", "Fulfilment rate over time", "fulfilment_hourly",
               "hour", ["fulfilment_rate"],
               "One series, so the title names it and no legend is needed.",
               fmt=PCT, grain="PT1H", legend=False),
    timeseries("outcomes-trend", "How every request ended", "fulfilment_hourly",
               "hour",
               ["completed_trips", "rider_cancels", "driver_cancels", "unmatched"],
               "Counts, stacked: the height is demand and the colours are what "
               "happened to it.",
               fmt=NUM, grain="PT1H", series="bar", stacked=True),
    timeseries("wait-trend", "Time to match, and rider wait at the kerb",
               "fulfilment_hourly", "hour", ["time_to_match_s", "rider_wait_s"],
               "Two series on one axis only because both are seconds.",
               fmt=SECONDS, grain="PT1H"),
    timeseries("acceptance-trend", "Acceptance rate over time",
               "dispatch_funnel_hourly", "hour", ["acceptance_rate"],
               "Offers accepted out of offers made, per hour.",
               fmt=PCT, grain="PT1H", legend=False),
    timeseries("offers-per-match-trend", "Offers per accepted match",
               "dispatch_funnel_hourly", "hour", ["offers_per_match"],
               "How many drivers dispatch had to ask. Rises before fulfilment falls.",
               fmt=",.2f", grain="PT1H", legend=False),
    timeseries("eta-trend", "Pickup ETA: every offer vs the accepted one",
               "dispatch_funnel_hourly", "hour", ["eta_offered_s", "eta_accepted_s"],
               "Read the gap. Offers go out nearest-first, so the accepting "
               "driver is among the furthest asked and the accepted line sitting "
               "above the offered line is the chain working.",
               fmt=SECONDS, grain="PT1H"),
    timeseries("revenue-trend", "Revenue", "trip_stats_daily", "day", ["gross_revenue"],
               "Money on its own axis - a count and an amount do not share one.",
               fmt=MONEY, legend=False),
    timeseries("trips-trend", "Completed trips", "trip_stats_daily", "day", ["trips"],
               "Volume on its own axis, beside revenue rather than on top of it.",
               fmt=NUM, legend=False),
    timeseries("take-rate-trend", "Take rate over time", "trip_stats_daily", "day",
               ["take_rate"],
               "Exact Decimal sums, cast to float only for the ratio.",
               fmt=PCT, legend=False),
    timeseries("duration-percentiles", "Trip duration p50 / p95",
               "trip_duration_percentiles_hourly", "hour", ["p50_s", "p95_s"],
               "Both are seconds. quantileMerge, never quantile: the stored "
               "column is a sketch, not a number.",
               fmt=SECONDS, grain="PT1H"),
    timeseries("utilization-trend", "Driver utilization",
               "driver_utilization_hourly", "hour", ["utilization"],
               "Share of online time spent on a trip, fleet-wide.",
               fmt=PCT, grain="PT1H", legend=False),

    table("zone-leaderboard", "Where demand goes unserved", "fulfilment_hourly",
          ["pickup_zone_id"],
          ["requests", "fulfilment_rate", "no_driver_rate", "time_to_match_s"],
          "Zones by how badly they are served. A table, not a chart, because "
          "each column carries its own unit.",
          order_desc=False),
    table("od-leaderboard", "Busiest origin-destination pairs", "od_matrix_daily",
          ["pickup_zone_id", "dropoff_zone_id"], ["trips", "gross_revenue", "km_total"],
          "Directly comparable against the real TLC od_pair_calibration the "
          "demand generator was built from."),
    table("funnel-by-zone", "The matching funnel, by zone",
          "dispatch_funnel_hourly", ["pickup_zone_id"],
          ["offers", "acceptance_rate", "offers_per_match", "eta_offered_s"],
          "Where the chain is working hardest for each ride."),
]


# ==========================================================================
# The dashboard
# ==========================================================================

MARKETPLACE = dashboard(
    "nus-marketplace",
    title="not-uber-service - marketplace",
    description=(
        "Fulfilment, cancellations, the matching funnel, money and duration, "
        "read from the warehouse rollups and never from raw events."
    ),
    comment=(
        "The analytical view. Grafana answers 'what is happening right now';\n"
        "this answers 'what has been happening, and why'.\n"
        "\n"
        "Every chart reads a rollup - never trip_events, never a position\n"
        "table, never PostgreSQL. The read-replica connection registered\n"
        "alongside this one is for SQL Lab exploration only, per its own\n"
        "docstring, and check-assets.py fails a chart that reaches for it.\n"
        "\n"
        "Editing a chart in the browser does NOT write back to these files.\n"
        "Export it and commit the change, the same contract the Grafana\n"
        "dashboards have."
    ),
    colours={
        # Keyed on the series label Superset draws, which is the metric's
        # verbose_name - not the column underneath it.
        "Completed": "#0ca30c",
        "Cancelled by rider": "#fab219",
        "Cancelled by driver": "#ec835a",
        "No driver found": "#d03b3b",
        "Revenue": "#3987e5",
        "Driver pay": "#d95926",
    },
    rows=[
        "## Is the marketplace working\n\n"
        "Six numbers that say whether riders got rides and whether the trips "
        "paid for themselves. Every rate is summed across both ClickHouse "
        "shards before any division.",
        [("fulfilment-rate", 2, 38), ("cancellation-rate", 2, 38),
         ("acceptance-rate", 2, 38), ("take-rate", 2, 38),
         ("trips-total", 2, 38), ("revenue-total", 2, 38)],

        "## Demand that went unserved\n\n"
        "Every request reaches one of four endings. Completed earns money; "
        "the other three are demand the platform failed to meet, and the zone "
        "table says where.",
        [("fulfilment-trend", 6, 50), ("outcomes-trend", 6, 50)],
        [("zone-leaderboard", 12, 55)],

        "## The matching funnel\n\n"
        "Dispatch offers a ride to one driver at a time with a deadline. "
        "These are the numbers that decision produces, and none of them "
        "existed while dispatch simply assigned a driver.",
        [("acceptance-trend", 4, 50), ("offers-per-match-trend", 4, 50),
         ("eta-trend", 4, 50)],
        [("funnel-by-zone", 12, 50)],

        "## How long riders waited\n\n"
        "Request to match, driver arriving to rider getting in, and the trip "
        "itself. The middle one is what the 'arrived' state was added to make "
        "answerable at all.",
        [("wait-trend", 6, 50), ("duration-percentiles", 6, 50)],

        "## Money and the fleet\n\n"
        "Revenue and volume are different scales, so they get two charts "
        "rather than two axes on one.",
        [("revenue-trend", 4, 50), ("trips-trend", 4, 50), ("take-rate-trend", 4, 50)],
        [("utilization-trend", 6, 50), ("od-leaderboard", 6, 50)],
    ],
)

DASHBOARDS = [MARKETPLACE]

METADATA = {
    "_path": "metadata.yaml",
    "_comment": None,
    "version": VERSION,
    "type": "Dashboard",
    "timestamp": "2026-09-26T00:00:00+00:00",
}

# The connection exists in this bundle only so the datasets have a uuid to
# attach to. The uri carries NO password - a password does not belong in a
# committed file - and --overwrite applies that passwordless uri to whatever
# connection is already registered. That is why init-superset.sh runs
# register_database.py AFTER this import and not before: the real credential
# from the environment has to be the last thing written. Reordering those two
# is what made every chart fail with ClickHouse code 516 once already.
DATABASE = {
    "_path": f"databases/{DATABASE_SLUG}.yaml",
    "_comment": None,
    "database_name": DATABASE_NAME,
    "sqlalchemy_uri": "clickhousedb://nus@nus-lb-a:8123/nus",
    "uuid": str(DATABASE_UUID),
    "version": VERSION,
    "expose_in_sqllab": True,
    "allow_ctas": False,
    "allow_cvas": False,
    "allow_dml": False,
    "allow_run_async": False,
    "cache_timeout": None,
    "extra": {},
}


def write() -> list[Path]:
    if OUT.exists():
        shutil.rmtree(OUT)
    written = []
    for document in [METADATA, DATABASE, *DATASETS, *CHARTS, *DASHBOARDS]:
        document = dict(document)
        path = OUT / document.pop("_path")
        comment = document.pop("_comment", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = ""
        if comment:
            header = "".join(f"# {line}\n" for line in comment.splitlines())
        path.write_text(header + to_yaml(document))
        written.append(path)
    return written


if __name__ == "__main__":
    for path in write():
        print(path.relative_to(HERE))
