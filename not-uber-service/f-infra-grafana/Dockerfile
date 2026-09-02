# Grafana with the ClickHouse data source already inside the image.
#
# Grafana can also download plugins at start time, but then every restart
# depends on the internet being up and on the plugin site answering. Baking
# it in means the image is identical everywhere and starts with no network
# calls at all — which is also what lets the container run with no outbound
# access.
#
# The install is verified in the same layer: `grafana cli` reports success
# even when it has written nothing useful, so the build asserts that the
# plugin directory actually exists rather than trusting the exit code.

ARG GRAFANA_IMAGE=grafana/grafana:12.4.10
FROM ${GRAFANA_IMAGE}

ARG GRAFANA_IMAGE
ARG CLICKHOUSE_PLUGIN_VERSION=4.21.2

LABEL org.opencontainers.image.title="nus/grafana" \
      org.opencontainers.image.description="Grafana with the ClickHouse data source baked in, for the not-uber-service stack" \
      org.opencontainers.image.source="https://github.com/bahman-farhadian/Meanul-Data-Studio" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="${GRAFANA_IMAGE}"

# The plugin directory belongs to the grafana user, and this is the only step
# that writes to it. The base image already drops to that user, so the build
# stays unprivileged throughout.
USER grafana

# The subcommand was renamed between Grafana majors (`grafana-cli` became
# `grafana cli`), so try the current spelling and fall back to the old one.
RUN (grafana cli --pluginsDir /var/lib/grafana/plugins plugins install grafana-clickhouse-datasource "${CLICKHOUSE_PLUGIN_VERSION}" \
     || grafana-cli --pluginsDir /var/lib/grafana/plugins plugins install grafana-clickhouse-datasource "${CLICKHOUSE_PLUGIN_VERSION}") \
 && test -d /var/lib/grafana/plugins/grafana-clickhouse-datasource \
 && echo "grafana-clickhouse-datasource ${CLICKHOUSE_PLUGIN_VERSION} installed"
