#!/usr/bin/env python3
"""Write the provisioned Grafana dashboard JSON this piece ships.

The JSON under provisioning/dashboards/json/ is what Grafana loads. This
script is the generator so the query text stays in one place; check-dashboards.py
validates the written files, not this module.
"""

from __future__ import annotations

import json
from pathlib import Path

DS = {"type": "grafana-clickhouse-datasource", "uid": "nus-clickhouse"}
PLUGIN = "4.21.2"
OUT = Path(__file__).resolve().parent / "provisioning" / "dashboards" / "json"
# Same-origin OSM raster from nus-tiles (HAProxy /tiles/ → tileserver-gl).
# No MapTiler, no API key, no third-party CDN.
LOCAL_XYZ = {
    "type": "xyz",
    "name": "NUS OSM",
    "config": {
        "url": "/tiles/styles/nus/{z}/{x}/{y}@2x.png",
        "attribution": "© OpenStreetMap",
        "minZoom": 0,
        "maxZoom": 14,
    },
}


def geomap_options(layer_name: str, *, lat: float = 40.75, lon: float = -73.98, zoom: int = 11) -> dict:
    return {
        "view": {
            "allLayers": True,
            "id": "coords",
            "lat": lat,
            "lon": lon,
            "zoom": zoom,
        },
        "controls": {
            "showZoom": True,
            "mouseWheelZoom": True,
            "showAttribution": True,
        },
        "basemap": LOCAL_XYZ,
        "layers": [
            {
                "type": "markers",
                "name": layer_name,
                "config": {
                    "style": {
                        "size": {"fixed": 7, "min": 3, "max": 12},
                        "color": {"fixed": "dark-green"},
                        "opacity": 0.9,
                        "symbol": {
                            "mode": "fixed",
                            "fixed": "img/icons/marker/circle.svg",
                        },
                    }
                },
                "location": {
                    "mode": "coords",
                    "latitude": "lat",
                    "longitude": "lon",
                },
                "tooltip": True,
            }
        ],
    }

# ClickHouse plugin: format 0 = time series, 1 = table (stat/table/geomap).
FMT_TS, FMT_TABLE = 0, 1


def target(sql: str, ref: str = "A", timeseries: bool = False) -> dict:
    return {
        "datasource": DS,
        "editorType": "sql",
        "format": FMT_TS if timeseries else FMT_TABLE,
        "queryType": "timeseries" if timeseries else "table",
        "rawSql": sql.strip() + "\n",
        "refId": ref,
        "pluginVersion": PLUGIN,
    }


def panel(
    pid: int,
    title: str,
    ptype: str,
    sql: str,
    x: int,
    y: int,
    w: int,
    h: int,
    timeseries: bool = False,
    extra: dict | None = None,
    description: str = "",
) -> dict:
    p = {
        "id": pid,
        "title": title,
        "type": ptype,
        "datasource": DS,
        "description": description,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(sql, timeseries=timeseries)],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {},
    }
    if extra:
        p.update(extra)
    return p


# A KPI is read as "is this number where it should be", so it ships with the
# band it is judged against rather than as a bare figure. The thresholds are
# the operating targets this project states, not decoration: steps are read
# bottom-up by Grafana, and the first step's value is always null.
def fields(unit: str, *, decimals: int | None = None, steps: list | None = None,
           minimum: float | None = None, maximum: float | None = None) -> dict:
    defaults: dict = {"unit": unit}
    if decimals is not None:
        defaults["decimals"] = decimals
    if minimum is not None:
        defaults["min"] = minimum
    if maximum is not None:
        defaults["max"] = maximum
    if steps is not None:
        defaults["thresholds"] = {"mode": "absolute", "steps": steps}
    return {"fieldConfig": {"defaults": defaults, "overrides": []}}


def bands(*pairs: tuple[str, float | None]) -> list[dict]:
    return [{"color": colour, "value": value} for colour, value in pairs]


# Higher is better: red below the first bound, green above the last.
def good_high(warn: float, good: float) -> list[dict]:
    return bands(("red", None), ("yellow", warn), ("green", good))


# Lower is better - a cancellation rate, a wait, an error.
def good_low(warn: float, bad: float) -> list[dict]:
    return bands(("green", None), ("yellow", warn), ("red", bad))


STAT = {
    "options": {
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "colorMode": "value",
        "graphMode": "area",
        "justifyMode": "auto",
        "orientation": "auto",
        "textMode": "auto",
    }
}


def stat(pid, title, sql, x, y, w, h, *, unit, steps, decimals=2, description=""):
    """One number with the band it is judged against, and its own sparkline."""
    p = panel(pid, title, "stat", sql, x, y, w, h, description=description)
    p.update(STAT)
    p.update(fields(unit, decimals=decimals, steps=steps))
    return p


def qvar(name: str, label: str, sql: str) -> dict:
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DS,
        "definition": sql,
        "query": sql,
        "refresh": 2,
        "sort": 1,
        "includeAll": False,
        "multi": False,
        "current": {},
        "hide": 0,
        "regex": "",
        "skipUrlSync": False,
        "options": [],
    }


def dashboard(
    *,
    uid: str,
    title: str,
    tags: list[str],
    time_from: str,
    refresh: str,
    panels: list[dict],
    templating: list[dict] | None = None,
    description: str = "",
    live: bool = False,
) -> dict:
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": tags,
        "timezone": "America/New_York",
        "schemaVersion": 39,
        "version": 11,
        "refresh": refresh,
        "liveNow": live,
        "editable": False,
        "graphTooltip": 1,
        "time": {"from": time_from, "to": "now"},
        "timepicker": {},
        "templating": {"list": templating or []},
        "annotations": {"list": []},
        "panels": panels,
        "links": [
            {"title": "Live ops", "type": "link", "url": "/d/nus-live-ops", "keepTime": True},
            {"title": "Driver", "type": "link", "url": "/d/nus-driver", "keepTime": True},
            {"title": "Trip", "type": "link", "url": "/d/nus-trip", "keepTime": True},
            {"title": "City", "type": "link", "url": "/d/nus-city", "keepTime": True},
            {"title": "History", "type": "link", "url": "/d/nus-history", "keepTime": True},
            {"title": "Marketplace", "type": "link", "url": "/d/nus-marketplace", "keepTime": True},
        ],
    }


# ---------------------------------------------------------------------------
# Live ops
# ---------------------------------------------------------------------------

OPEN_STATUSES = (
    "'requested', 'matched', 'accepted', 'en_route_pickup', 'in_progress'"
)

LIVE_OPS = dashboard(
    uid="nus-live-ops",
    title="NUS / Live operations",
    description=(
        "What is happening now: open trips, ingest, last driver positions, "
        "warehouse freshness. ClickHouse only."
    ),
    tags=["nus", "live"],
    time_from="now-15m",
    refresh="10s",
    live=True,
    panels=[
        panel(
            1,
            "Open trips by last status",
            "stat",
            f"""
SELECT status, count() AS trips
FROM (
    SELECT trip_id, argMax(status, event_time) AS status
    FROM nus.trip_events
    WHERE event_time >= now() - INTERVAL 6 HOUR
    GROUP BY trip_id
)
WHERE status IN ({OPEN_STATUSES})
GROUP BY status
""",
            0, 0, 16, 6,
            extra={
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": True},
                    "colorMode": "background",
                    "graphMode": "none",
                    "justifyMode": "auto",
                    "orientation": "auto",
                    "textMode": "auto",
                }
            },
        ),
        panel(
            2,
            "trip_events lag (seconds behind now)",
            "stat",
            """
SELECT dateDiff('second', max(event_time), now()) AS lag_s
FROM nus.trip_events
""",
            16, 0, 8, 3,
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 30},
                                {"color": "red", "value": 120},
                            ],
                        },
                    },
                    "overrides": [],
                }
            },
        ),
        panel(
            3,
            "driver_positions lag (seconds behind now)",
            "stat",
            """
SELECT dateDiff('second', max(event_time), now()) AS lag_s
FROM nus.driver_positions
""",
            16, 3, 8, 3,
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 30},
                                {"color": "red", "value": 120},
                            ],
                        },
                    },
                    "overrides": [],
                }
            },
        ),
        panel(
            4,
            "Trip events per minute",
            "timeseries",
            """
SELECT
    $__timeInterval(event_time) AS time,
    count() AS events
FROM nus.trip_events
WHERE $__timeFilter(event_time)
GROUP BY time
ORDER BY time
""",
            0, 6, 12, 8,
            timeseries=True,
        ),
        panel(
            5,
            "Driver positions per minute",
            "timeseries",
            """
SELECT
    $__timeInterval(event_time) AS time,
    count() AS events
FROM nus.driver_positions
WHERE $__timeFilter(event_time)
GROUP BY time
ORDER BY time
""",
            12, 6, 12, 8,
            timeseries=True,
        ),
        panel(
            6,
            "Completes / no-driver / cancels",
            "timeseries",
            """
SELECT
    $__timeInterval(event_time) AS time,
    status,
    count() AS events
FROM nus.trip_events
WHERE $__timeFilter(event_time)
  AND status IN ('completed', 'no_driver_found', 'cancelled_by_passenger', 'cancelled_by_driver')
GROUP BY time, status
ORDER BY time
""",
            0, 14, 24, 8,
            timeseries=True,
        ),
        panel(
            7,
            "Last driver positions (2 minutes)",
            "geomap",
            """
SELECT
    driver_id,
    argMax(lat, event_time) AS lat,
    argMax(lon, event_time) AS lon,
    argMax(status, event_time) AS status,
    max(event_time) AS last_seen
FROM nus.driver_positions
WHERE event_time >= now() - INTERVAL 2 MINUTE
GROUP BY driver_id
""",
            0, 22, 14, 12,
            extra={"options": geomap_options("Drivers")},
        ),
        panel(
            8,
            "In-progress trips",
            "table",
            """
SELECT
    trip_id,
    argMax(driver_id, event_time) AS driver_id,
    argMax(rider_id, event_time) AS rider_id,
    argMax(pickup_zone_id, event_time) AS pickup_zone,
    argMax(dropoff_zone_id, event_time) AS dropoff_zone,
    argMax(predicted_duration_s, event_time) AS predicted_s,
    argMax(fare_estimate, event_time) AS fare_estimate,
    max(event_time) AS last_event
FROM nus.trip_events
WHERE event_time >= now() - INTERVAL 6 HOUR
GROUP BY trip_id
HAVING argMax(status, event_time) = 'in_progress'
ORDER BY last_event DESC
LIMIT 100
""",
            14, 22, 10, 12,
        ),
        panel(
            9,
            "Fleet last status (2 minutes)",
            "table",
            """
SELECT status, count() AS drivers
FROM (
    SELECT driver_id, argMax(status, event_time) AS status
    FROM nus.driver_positions
    WHERE event_time >= now() - INTERVAL 2 MINUTE
    GROUP BY driver_id
)
GROUP BY status
""",
            0, 34, 24, 8,
        ),
    ],
)

# ---------------------------------------------------------------------------
# Driver inspector
# ---------------------------------------------------------------------------

DRIVER = dashboard(
    uid="nus-driver",
    title="NUS / Driver inspector",
    description="Lookup one driver_id: trail, status, speed, utilization.",
    tags=["nus", "live", "driver"],
    time_from="now-3h",
    refresh="10s",
    live=True,
    templating=[
        qvar(
            "driver_id",
            "Driver",
            "SELECT DISTINCT toString(driver_id) FROM nus.driver_positions "
            "WHERE event_time >= now() - INTERVAL 1 HOUR ORDER BY 1 LIMIT 500",
        )
    ],
    panels=[
        panel(
            1,
            "Latest position",
            "stat",
            """
SELECT
    argMax(status, event_time) AS status,
    argMax(lat, event_time) AS lat,
    argMax(lon, event_time) AS lon,
    argMax(speed_kmh, event_time) AS speed_kmh,
    argMax(trip_id, event_time) AS trip_id
FROM nus.driver_positions
WHERE driver_id = '${driver_id}'
""",
            0, 0, 24, 4,
        ),
        panel(
            2,
            "Map trail",
            "geomap",
            """
SELECT event_time, lat, lon, status, speed_kmh, trip_id
FROM nus.driver_positions
WHERE driver_id = '${driver_id}'
  AND $__timeFilter(event_time)
ORDER BY event_time
""",
            0, 4, 12, 12,
            extra={"options": geomap_options("Trail")},
        ),
        panel(
            3,
            "Status over time",
            "table",
            """
SELECT event_time, status, speed_kmh, heading_deg, zone_id, trip_id, lat, lon
FROM nus.driver_positions
WHERE driver_id = '${driver_id}'
  AND $__timeFilter(event_time)
ORDER BY event_time DESC
LIMIT 500
""",
            12, 4, 12, 12,
        ),
        panel(
            4,
            "Speed",
            "timeseries",
            """
SELECT event_time AS time, speed_kmh
FROM nus.driver_positions
WHERE driver_id = '${driver_id}'
  AND $__timeFilter(event_time)
ORDER BY time
""",
            0, 16, 12, 8,
            timeseries=True,
        ),
        panel(
            5,
            "Hourly utilization (busy / online ticks)",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(busy_ticks) / sum(online_ticks) AS utilization
FROM nus.driver_utilization_hourly
WHERE driver_id = '${driver_id}'
  AND $__timeFilter(hour)
GROUP BY hour
HAVING sum(online_ticks) > 0
ORDER BY time
""",
            12, 16, 12, 8,
            timeseries=True,
        ),
    ],
)

# ---------------------------------------------------------------------------
# Trip / rider inspector
# ---------------------------------------------------------------------------

TRIP = dashboard(
    uid="nus-trip",
    title="NUS / Trip inspector",
    description="Status walk, rider trail, fares for one trip_id.",
    tags=["nus", "live", "trip"],
    time_from="now-6h",
    refresh="10s",
    live=True,
    templating=[
        qvar(
            "trip_id",
            "Trip",
            "SELECT DISTINCT toString(trip_id) FROM nus.trip_events "
            "WHERE event_time >= now() - INTERVAL 2 HOUR ORDER BY 1 DESC LIMIT 500",
        )
    ],
    panels=[
        panel(
            1,
            "Status walk",
            "table",
            """
SELECT
    event_time,
    status,
    driver_id,
    rider_id,
    pickup_zone_id,
    dropoff_zone_id,
    route_km,
    predicted_duration_s,
    actual_duration_s,
    duration_delta_s,
    surge_multiplier,
    hotspot_score,
    is_hotspot_trip,
    fare_estimate,
    fare_final
FROM nus.trip_events
WHERE trip_id = '${trip_id}'
ORDER BY event_time
""",
            0, 0, 24, 10,
        ),
        panel(
            2,
            "Rider positions",
            "geomap",
            """
SELECT event_time, lat, lon, accuracy_m, zone_id
FROM nus.rider_positions
WHERE trip_id = '${trip_id}'
ORDER BY event_time
""",
            0, 10, 12, 10,
            extra={"options": geomap_options("Rider")},
        ),
        panel(
            3,
            "Predicted vs actual duration (completed row)",
            "stat",
            """
SELECT
    predicted_duration_s,
    actual_duration_s,
    duration_delta_s,
    fare_estimate,
    fare_final
FROM nus.trip_events
WHERE trip_id = '${trip_id}'
  AND status = 'completed'
ORDER BY event_time DESC
LIMIT 1
""",
            12, 10, 12, 10,
        ),
    ],
)

# ---------------------------------------------------------------------------
# City now
# ---------------------------------------------------------------------------

CITY = dashboard(
    uid="nus-city",
    title="NUS / City now",
    description="Zone demand, surge, congestion. Scores near 0 at the small seed are expected.",
    tags=["nus", "live", "city"],
    time_from="now-3h",
    refresh="30s",
    live=True,
    templating=[
        qvar(
            "zone_id",
            "Zone",
            "SELECT DISTINCT toString(zone_id) FROM nus.hotspot_history "
            "WHERE computed_at >= now() - INTERVAL 1 HOUR ORDER BY 1 LIMIT 300",
        )
    ],
    panels=[
        panel(
            1,
            "Latest demand by zone",
            "table",
            """
SELECT
    zone_id,
    demand_score,
    open_requests,
    available_drivers,
    surge_multiplier,
    period,
    last_computed
FROM (
    SELECT
        zone_id,
        argMax(demand_score, computed_at) AS demand_score,
        argMax(open_requests, computed_at) AS open_requests,
        argMax(available_drivers, computed_at) AS available_drivers,
        argMax(surge_multiplier, computed_at) AS surge_multiplier,
        argMax(period, computed_at) AS period,
        max(computed_at) AS last_computed
    FROM nus.hotspot_history
    WHERE computed_at >= now() - INTERVAL 10 MINUTE
    GROUP BY zone_id
) AS latest
ORDER BY demand_score DESC
""",
            0, 0, 24, 10,
        ),
        panel(
            2,
            "Demand score (selected zone)",
            "timeseries",
            """
SELECT computed_at AS time, demand_score, surge_multiplier
FROM nus.hotspot_history
WHERE zone_id = '${zone_id}'
  AND $__timeFilter(computed_at)
ORDER BY time
""",
            0, 10, 12, 8,
            timeseries=True,
        ),
        panel(
            3,
            "Open requests vs free drivers (selected zone)",
            "timeseries",
            """
SELECT computed_at AS time, open_requests, available_drivers
FROM nus.hotspot_history
WHERE zone_id = '${zone_id}'
  AND $__timeFilter(computed_at)
ORDER BY time
""",
            12, 10, 12, 8,
            timeseries=True,
        ),
        panel(
            4,
            "Congestion factor (selected zone)",
            "timeseries",
            """
SELECT computed_at AS time, congestion_factor, speed_samples, segments_updated
FROM nus.segment_traffic_history
WHERE zone_id = '${zone_id}'
  AND $__timeFilter(computed_at)
ORDER BY time
""",
            0, 18, 24, 8,
            timeseries=True,
        ),
    ],
)

# ---------------------------------------------------------------------------
# Historical / analytical from ClickHouse rollups
# ---------------------------------------------------------------------------

HISTORY = dashboard(
    uid="nus-history",
    title="NUS / ClickHouse history",
    description=(
        "Hourly and daily rollups. SummingMergeTree panels use sum(); "
        "percentiles use quantileMerge(); distinct counts use uniqMerge()."
    ),
    tags=["nus", "history"],
    time_from="now-7d",
    refresh="1m",
    live=False,
    panels=[
        panel(
            1,
            "Completed trips and revenue (hourly, sum across shards)",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(completed_trips) AS completed_trips,
    sum(revenue) AS revenue
FROM nus.trip_stats_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
ORDER BY time
""",
            0, 0, 24, 8,
            timeseries=True,
        ),
        panel(
            2,
            "Daily completed trips and revenue (sum)",
            "timeseries",
            """
SELECT
    toDateTime(day) AS time,
    sum(completed_trips) AS completed_trips,
    sum(revenue) AS revenue
FROM nus.trip_stats_daily
WHERE day >= toDate($__fromTime) AND day <= toDate($__toTime)
GROUP BY day
ORDER BY time
""",
            0, 8, 12, 8,
            timeseries=True,
        ),
        panel(
            3,
            "Trip duration p50 / p95 (quantileMerge)",
            "timeseries",
            """
SELECT
    hour AS time,
    quantileMerge(0.5)(p50_state) AS p50_s,
    quantileMerge(0.95)(p95_state) AS p95_s
FROM nus.trip_duration_percentiles_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
ORDER BY time
""",
            12, 8, 12, 8,
            timeseries=True,
        ),
        panel(
            4,
            "Driver utilization (busy / online ticks, sum)",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(busy_ticks) / sum(online_ticks) AS utilization
FROM nus.driver_utilization_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
HAVING sum(online_ticks) > 0
ORDER BY time
""",
            0, 16, 12, 8,
            timeseries=True,
        ),
        panel(
            5,
            "Distinct drivers and riders (uniqMerge)",
            "timeseries",
            """
SELECT
    hour AS time,
    uniqMerge(driver_state) AS drivers,
    uniqMerge(rider_state) AS riders
FROM nus.active_entities_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
ORDER BY time
""",
            12, 16, 12, 8,
            timeseries=True,
        ),
        panel(
            6,
            "Overrun share of completed trips (hourly, sum)",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(overrun_trips) / sum(completed_trips) AS overrun_share
FROM nus.trip_stats_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
HAVING sum(completed_trips) > 0
ORDER BY time
""",
            0, 24, 12, 8,
            timeseries=True,
        ),
        panel(
            7,
            "Top pickup zones today (daily rollup, sum)",
            "table",
            """
SELECT
    pickup_zone_id,
    sum(completed_trips) AS completed_trips,
    sum(revenue) AS revenue,
    sum(route_km_total) AS route_km
FROM nus.trip_stats_daily
WHERE day = today()
GROUP BY pickup_zone_id
ORDER BY completed_trips DESC
LIMIT 20
""",
            12, 24, 12, 8,
        ),
        panel(
            8,
            "Origin-destination (daily, sum)",
            "table",
            """
SELECT
    pickup_zone_id,
    dropoff_zone_id,
    sum(completed_trips) AS completed_trips,
    sum(revenue) AS revenue
FROM nus.od_matrix_daily
WHERE day = today()
GROUP BY pickup_zone_id, dropoff_zone_id
ORDER BY completed_trips DESC
LIMIT 30
""",
            0, 32, 24, 10,
        ),
    ],
)



# ---------------------------------------------------------------------------
# Marketplace
#
# The five dashboards before this one answer "is the platform running":
# live ops, the driver and trip inspectors, the city, and the rollups. None
# of them answers "is the marketplace working", which is a different
# question with its own numbers - a platform can have every service healthy
# and still fail three riders in ten.
#
# Two rules hold across every panel here, and check-dashboards.py enforces
# both:
#
#   Every rate is sum(x) / sum(y), never avg(rate). The tables underneath
#   are SummingMergeTree: a row is a partial sum until a merge that may not
#   have happened yet, so an average over rows is an average over an
#   arbitrary grouping. Summing first and dividing once is the only spelling
#   that is correct at every merge state.
#
#   No panel puts two different scales on one chart. Trips and revenue, or a
#   rate and a count, are two charts - the smaller series is invisible
#   otherwise, and a second y-axis just moves the lie.
# ---------------------------------------------------------------------------

# Colour follows the outcome, never its rank in the result, so a zone filter
# that drops a series cannot repaint the ones that survive. Completed is the
# only good outcome; the three failures are graded by how much of the
# platform's promise was already spent when they happened.
OUTCOME_COLOURS = {
    "completed": "green",
    "cancelled_by_passenger": "yellow",
    "cancelled_by_driver": "orange",
    "no_driver_found": "red",
}


def by_name(colours: dict[str, str]) -> list[dict]:
    return [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": colour}}],
        }
        for name, colour in colours.items()
    ]


STACKED_BARS = {
    "drawStyle": "bars",
    "fillOpacity": 80,
    "lineWidth": 0,
    "stacking": {"mode": "normal", "group": "A"},
}

MARKETPLACE = dashboard(
    uid="nus-marketplace",
    title="NUS / Marketplace",
    description=(
        "Fulfilment, cancellations, the matching funnel, take rate and ETA "
        "accuracy. Every rate is sum()/sum() over the rollups, never an "
        "average of rates."
    ),
    tags=["nus", "marketplace"],
    time_from="now-24h",
    refresh="1m",
    live=False,
    panels=[
        stat(
            1,
            "Fulfilment rate",
            """
SELECT sum(completed) / sum(trips_ended) AS fulfilment_rate
FROM nus.fulfilment_hourly
WHERE $__timeFilter(hour)
HAVING sum(trips_ended) > 0
""",
            0, 0, 6, 5,
            unit="percentunit",
            steps=good_high(0.80, 0.90),
            description=(
                "Completed trips as a share of every request that reached an "
                "ending, bucketed on when the ride was ASKED for. This is the "
                "one number that counts requests nobody served - every other "
                "rollup in this warehouse filters status = 'completed' and so "
                "cannot see them."
            ),
        ),
        stat(
            2,
            "Cancellation rate",
            """
SELECT
    (sum(cancelled_by_passenger) + sum(cancelled_by_driver)) / sum(trips_ended)
        AS cancellation_rate
FROM nus.fulfilment_hourly
WHERE $__timeFilter(hour)
HAVING sum(trips_ended) > 0
""",
            6, 0, 6, 5,
            unit="percentunit",
            steps=good_low(0.10, 0.20),
            description=(
                "Both sides together. A trip that ended because nobody could "
                "be found is NOT a cancellation - it is counted separately, "
                "because the fix for it is supply, not behaviour."
            ),
        ),
        stat(
            3,
            "Driver acceptance rate",
            """
SELECT sum(offers_accepted) / sum(offers_made) AS acceptance_rate
FROM nus.dispatch_funnel_hourly
WHERE $__timeFilter(hour)
HAVING sum(offers_made) > 0
""",
            12, 0, 6, 5,
            unit="percentunit",
            steps=good_high(0.45, 0.60),
            description=(
                "Offers accepted out of offers made. Dispatch offers a ride "
                "to one candidate at a time with a deadline; this number did "
                "not exist while dispatch simply assigned."
            ),
        ),
        stat(
            4,
            "Take rate",
            """
SELECT
    toFloat64(sum(revenue) - sum(payout_total)) / toFloat64(sum(revenue))
        AS take_rate
FROM nus.trip_stats_hourly
WHERE $__timeFilter(hour)
HAVING sum(revenue) > 0
""",
            18, 0, 6, 5,
            unit="percentunit",
            steps=good_low(0.25, 0.30),
            description=(
                "What the platform keeps of every fare. Both sums are "
                "Decimal64(2) and exact; the cast to Float64 happens on the "
                "RATIO, never on the money - Decimal divided by Decimal "
                "truncates to the left operand's scale, which would quietly "
                "round the answer to two places."
            ),
        ),
        panel(
            5,
            "Fulfilment rate by hour",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(completed) / sum(trips_ended) AS fulfilment_rate
FROM nus.fulfilment_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
HAVING sum(trips_ended) > 0
ORDER BY time
""",
            0, 5, 12, 8,
            timeseries=True,
            description=(
                "One series, so the title names it and no legend box is "
                "needed. The band behind it is the same one the stat above "
                "is judged against."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "percentunit",
                        "min": 0,
                        "max": 1,
                        "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10},
                        "thresholds": {"mode": "absolute", "steps": good_high(0.80, 0.90)},
                    },
                    "overrides": [],
                },
                "options": {"legend": {"showLegend": False}, "tooltip": {"mode": "single"}},
            },
        ),
        panel(
            6,
            "How every request ended (hourly)",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(completed)               AS completed,
    sum(cancelled_by_passenger)  AS cancelled_by_passenger,
    sum(cancelled_by_driver)     AS cancelled_by_driver,
    sum(no_driver_found)         AS no_driver_found
FROM nus.fulfilment_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
ORDER BY time
""",
            12, 5, 12, 8,
            timeseries=True,
            description=(
                "Counts, stacked, so the height is demand and the colours are "
                "what happened to it. Each outcome keeps its colour whatever "
                "else is on the chart."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {"unit": "short", "custom": STACKED_BARS},
                    "overrides": by_name(OUTCOME_COLOURS),
                },
                "options": {
                    "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi", "sort": "desc"},
                },
            },
        ),
        panel(
            7,
            "Time to match, and rider wait at the kerb",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(match_s_sum) / sum(matched_trips) AS time_to_match_s,
    sum(wait_s_sum)  / sum(waited_trips)  AS rider_wait_s
FROM nus.fulfilment_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
HAVING sum(matched_trips) > 0 AND sum(waited_trips) > 0
ORDER BY time
""",
            0, 13, 12, 8,
            timeseries=True,
            description=(
                "Two series on one axis only because both are seconds. Wait "
                "at the kerb is the gap between the driver arriving and the "
                "rider getting in - the measure the 'arrived' state was added "
                "to make answerable, and what a real platform charges a "
                "per-minute wait fee against."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0},
                    },
                    "overrides": by_name({"time_to_match_s": "blue", "rider_wait_s": "purple"}),
                },
                "options": {
                    "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
            },
        ),
        panel(
            8,
            "Why trips were cancelled",
            "barchart",
            """
SELECT
    cancellation_reason AS reason,
    count() AS trips
FROM nus.trip_facts FINAL
WHERE $__timeFilter(event_time)
  AND final_status IN ('cancelled_by_passenger', 'cancelled_by_driver')
  AND cancellation_reason IS NOT NULL
GROUP BY reason
ORDER BY trips DESC
""",
            12, 13, 12, 8,
            description=(
                "Support and analytics need 'the rider never came out' apart "
                "from 'the driver found something better': very different "
                "problems that look identical without the reason. FINAL is "
                "deliberate - trip_facts is a ReplacingMergeTree, so a count "
                "without it is an upper bound, and every trip's rows land on "
                "one shard, which is what makes a local FINAL correct here."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {"unit": "short", "custom": {"fillOpacity": 80, "lineWidth": 0}},
                    "overrides": [],
                },
                "options": {
                    "orientation": "horizontal",
                    "xTickLabelRotation": 0,
                    "legend": {"showLegend": False},
                    "tooltip": {"mode": "single"},
                },
            },
        ),
        panel(
            9,
            "Driver acceptance rate by hour",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(offers_accepted) / sum(offers_made) AS acceptance_rate
FROM nus.dispatch_funnel_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
HAVING sum(offers_made) > 0
ORDER BY time
""",
            0, 21, 8, 8,
            timeseries=True,
            description="Offers accepted out of offers made, per hour.",
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "percentunit",
                        "min": 0,
                        "max": 1,
                        "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10},
                        "thresholds": {"mode": "absolute", "steps": good_high(0.45, 0.60)},
                    },
                    "overrides": [],
                },
                "options": {"legend": {"showLegend": False}, "tooltip": {"mode": "single"}},
            },
        ),
        panel(
            10,
            "Offers per accepted match",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(offers_made) / sum(offers_accepted) AS offers_per_match
FROM nus.dispatch_funnel_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
HAVING sum(offers_accepted) > 0
ORDER BY time
""",
            8, 21, 8, 8,
            timeseries=True,
            description=(
                "How many drivers dispatch had to ask before one took the "
                "ride. Rising means the chain is working harder for the same "
                "trip, which is the earliest visible sign of thin supply - "
                "well before fulfilment falls."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "decimals": 2,
                        "min": 0,
                        "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10},
                        "thresholds": {"mode": "absolute", "steps": good_low(2.5, 4.0)},
                    },
                    "overrides": [],
                },
                "options": {"legend": {"showLegend": False}, "tooltip": {"mode": "single"}},
            },
        ),
        panel(
            11,
            "Pickup ETA: every offer vs the accepted one",
            "timeseries",
            """
SELECT
    hour AS time,
    sum(eta_seconds_sum) / sum(eta_offers)      AS eta_offered_s,
    sum(accepted_eta_sum) / sum(offers_accepted) AS eta_accepted_s
FROM nus.dispatch_funnel_hourly
WHERE $__timeFilter(hour)
GROUP BY hour
HAVING sum(eta_offers) > 0 AND sum(offers_accepted) > 0
ORDER BY time
""",
            16, 21, 8, 8,
            timeseries=True,
            description=(
                "Both in seconds, so one axis is honest. Read the GAP, not "
                "the levels: dispatch offers nearest-first, so the driver who "
                "accepts is by construction among the furthest asked, and the "
                "accepted line sitting above the offered line is the chain "
                "working as designed rather than a fault."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0},
                    },
                    "overrides": by_name({"eta_offered_s": "blue", "eta_accepted_s": "orange"}),
                },
                "options": {
                    "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
            },
        ),
        panel(
            12,
            "Does surge lift acceptance",
            "barchart",
            """
SELECT
    multiIf(surge_multiplier < 1.05, '1.00 (flat)',
            surge_multiplier < 1.25, '1.05 - 1.25',
            surge_multiplier < 1.50, '1.25 - 1.50',
            surge_multiplier < 2.00, '1.50 - 2.00',
                                     '2.00 +')       AS surge_band,
    countIf(status = 'accepted') / count()           AS acceptance_rate
FROM nus.dispatch_offers
WHERE $__timeFilter(offered_at)
  AND surge_multiplier IS NOT NULL
GROUP BY surge_band
HAVING count() > 0
ORDER BY surge_band
""",
            0, 29, 12, 8,
            description=(
                "The question surge exists to answer. Bucketed at the OFFER, "
                "using the multiplier that was in force when the driver was "
                "asked - not recomputed later. A flat line across the bands "
                "means surge is not buying supply, whatever the price says."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "percentunit",
                        "min": 0,
                        "max": 1,
                        "custom": {"fillOpacity": 80, "lineWidth": 0},
                    },
                    "overrides": [],
                },
                "options": {
                    "orientation": "vertical",
                    "xTickLabelRotation": 0,
                    "legend": {"showLegend": False},
                    "tooltip": {"mode": "single"},
                },
            },
        ),
        panel(
            13,
            "Duration error: actual minus predicted",
            "timeseries",
            """
SELECT
    toStartOfHour(ended_at) AS time,
    quantile(0.5)(toInt64(actual_duration_s) - toInt64(predicted_duration_s))
        AS p50_error_s,
    quantile(0.9)(toInt64(actual_duration_s) - toInt64(predicted_duration_s))
        AS p90_error_s
FROM nus.trip_facts
WHERE $__timeFilter(event_time)
  AND final_status = 'completed'
  AND predicted_duration_s IS NOT NULL
  AND actual_duration_s IS NOT NULL
GROUP BY time
ORDER BY time
""",
            12, 29, 12, 8,
            timeseries=True,
            description=(
                "pgRouting promises a duration before the trip starts; this "
                "is what it cost. Zero is a perfect promise and positive "
                "means the traffic model is behind the street. Cast to Int64 "
                "before subtracting: both columns are unsigned, and an "
                "unsigned difference wraps a small early arrival into four "
                "billion seconds. No FINAL here on purpose - a duplicate "
                "shifts a quantile by nothing, and FINAL over a raw table "
                "every hour is not free."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0},
                    },
                    "overrides": by_name({"p50_error_s": "blue", "p90_error_s": "red"}),
                },
                "options": {
                    "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
            },
        ),
        panel(
            14,
            "Where demand goes unserved",
            "table",
            """
SELECT
    pickup_zone_id,
    sum(trips_ended)                       AS requests,
    sum(completed) / sum(trips_ended)      AS fulfilment_rate,
    sum(no_driver_found) / sum(trips_ended) AS no_driver_rate,
    sum(match_s_sum) / sum(matched_trips)  AS time_to_match_s
FROM nus.fulfilment_hourly
WHERE $__timeFilter(hour)
GROUP BY pickup_zone_id
HAVING sum(trips_ended) >= 20 AND sum(matched_trips) > 0
ORDER BY fulfilment_rate ASC
LIMIT 25
""",
            0, 37, 24, 9,
            description=(
                "Worst zone first, because that is the one to act on. The "
                "HAVING floor keeps a zone with three requests and one "
                "failure off the top of the list - at 25 zones deep, small "
                "denominators are the only way to get noise up here."
            ),
            extra={
                "fieldConfig": {
                    "defaults": {"unit": "short"},
                    "overrides": [
                        {
                            "matcher": {"id": "byName", "options": "fulfilment_rate"},
                            "properties": [
                                {"id": "unit", "value": "percentunit"},
                                {"id": "decimals", "value": 3},
                                {
                                    "id": "custom.cellOptions",
                                    "value": {"type": "color-background", "mode": "gradient"},
                                },
                                {
                                    "id": "thresholds",
                                    "value": {"mode": "absolute", "steps": good_high(0.80, 0.90)},
                                },
                            ],
                        },
                        {
                            "matcher": {"id": "byName", "options": "no_driver_rate"},
                            "properties": [
                                {"id": "unit", "value": "percentunit"},
                                {"id": "decimals", "value": 3},
                            ],
                        },
                        {
                            "matcher": {"id": "byName", "options": "time_to_match_s"},
                            "properties": [
                                {"id": "unit", "value": "s"},
                                {"id": "decimals", "value": 1},
                            ],
                        },
                    ],
                },
                "options": {"showHeader": True, "footer": {"show": False}},
            },
        ),
    ],
)


ALL = [LIVE_OPS, DRIVER, TRIP, CITY, HISTORY, MARKETPLACE]


def write() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for dash in ALL:
        path = OUT / f"{dash['uid']}.json"
        path.write_text(json.dumps(dash, indent=2) + "\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for path in write():
        print(path.relative_to(Path(__file__).resolve().parent))
