# f-infra-grafana — the live view

One Grafana: `hailpath-grafana`. It is the only front end. Dashboards read
`hailpath-clickhouse`. Tiles for geomap: `hailpath-tiles` (OSM now; LION
later).

Image, plugin pin, and memory are in the root `.env`. UTC.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | `hailpath-grafana` and `hailpath-tiles`. |
| `plugin-prepare.sh` | ClickHouse plugin into `hailpath-grafana-plugins`. |
| `provisioning/datasources/clickhouse.yaml` | Native 9000 on `hailpath-clickhouse`. |
| `provisioning/dashboards/json/*.json` | Hailpath dashboards. |
| `tiles/` | OSM MBTiles + tileserver-gl config. |
| `check-dashboards.py` | Datasource uid and warehouse table names. |
| `.env.example` | Standalone copy of the settings this piece reads. |

## Verify

From `meanul-hailpath/`:

```bash
make grafana-health
make tiles-health
make grafana-check
```

`make verify-grafana` runs each panel against ClickHouse. Empty tables
fail until bootstrap writes rows.

## Connecting

User `GRAFANA_ADMIN_USER`, password `GRAFANA_ADMIN_PASSWORD`. Host port
`PROXY_GRAFANA_PORT` (default 3000). Tiles: `PROXY_TILES_PORT` (default
8080; this laptop uses 18080). Geomap XYZ is
`http://127.0.0.1:<PROXY_TILES_PORT>/styles/hailpath/{z}/{x}/{y}@2x.png`.
After changing that port, run `python3 f-infra-grafana/render_dashboards.py`.

Dashboards: **Dashboards → Hailpath**. Home is live operations.

Standalone (ClickHouse must already be up):

```bash
docker network create hailpath-net
cp .env.example .env
docker compose up -d
```

## Plugin pin

`make grafana-plugin-prepare` installs `GRAFANA_CLICKHOUSE_PLUGIN_VERSION`
into the host plugin directory. A pin change replaces the old plugin.

## Teardown

```bash
docker compose down      # keep Grafana's database and the plugin dir
docker compose down -v   # drop the volume entry; bind-mount files stay until make destroy
```
