# Grafana with the ClickHouse data source already inside the image.
#
# Grafana can also download plugins at start time, but then every restart
# depends on the internet being up and on the plugin site answering. Baking
# it in means the image is the same everywhere and starts with no network
# calls at all.

ARG GRAFANA_IMAGE=grafana/grafana:11.6.0
FROM ${GRAFANA_IMAGE}

ARG CLICKHOUSE_PLUGIN_VERSION=4.5.1

# The command changed name between Grafana versions, so try the new one and
# fall back to the old one. One of the two exists in any supported image.
RUN (grafana cli --pluginsDir /var/lib/grafana/plugins plugins install grafana-clickhouse-datasource ${CLICKHOUSE_PLUGIN_VERSION}) \
    || (grafana-cli --pluginsDir /var/lib/grafana/plugins plugins install grafana-clickhouse-datasource ${CLICKHOUSE_PLUGIN_VERSION})
