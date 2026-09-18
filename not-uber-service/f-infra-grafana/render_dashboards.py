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
) -> dict:
    p = {
        "id": pid,
        "title": title,
        "type": ptype,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(sql, timeseries=timeseries)],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {},
    }
    if extra:
        p.update(extra)
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


ALL = [LIVE_OPS, DRIVER, TRIP, CITY, HISTORY]


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
