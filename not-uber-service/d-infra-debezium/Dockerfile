# Kafka Connect with the Debezium PostgreSQL connector, plus Confluent's
# Avro converter.
#
# Why the extra step: the Debezium image ships its connectors but not
# Confluent's Avro converter, because the two are published under different
# licences. Without it, Connect could only write JSON, and the cdc.* topics
# would be encoded differently from every other topic in the stack. The
# converter is a plain set of jars, so it is added to the plugin directory
# here, at build time, rather than fetched at run time.
#
# AVRO_CONVERTER_VERSION must track the Schema Registry version pinned in
# c-infra-kafka — they are two halves of the same Confluent release.

ARG CONNECT_IMAGE=quay.io/debezium/connect:3.6.2.Final
FROM ${CONNECT_IMAGE}

ARG CONNECT_IMAGE
ARG AVRO_CONVERTER_VERSION=8.3.1
ARG AVRO_CONVERTER_URL=https://packages.confluent.io/api/hub/confluentinc/kafka-connect-avro-converter/versions/${AVRO_CONVERTER_VERSION}/confluentinc-kafka-connect-avro-converter-${AVRO_CONVERTER_VERSION}.zip

LABEL org.opencontainers.image.title="nus/debezium-connect" \
      org.opencontainers.image.description="Debezium Kafka Connect with the Confluent Avro converter, for the not-uber-service CDC path" \
      org.opencontainers.image.source="https://github.com/bahman-farhadian/Meanul-Data-Studio" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="${CONNECT_IMAGE}"

USER root

# pipefail matters below: the jar copy is piped, and without it a failure on
# the left of a pipe is hidden by a successful command on the right.
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

# unzip is not guaranteed to be in the base image. Install it if a package
# manager is there; the build only needs it for this one step.
RUN (microdnf install -y unzip && microdnf clean all) \
 || (dnf install -y unzip && dnf clean all) \
 || echo "no package manager found - relying on unzip already being present"

# The archive holds the converter and everything it needs. Only the jars are
# kept; documentation and examples are dropped. The build then asserts that
# jars actually landed — an empty plugin directory would otherwise only show
# up as a connector that refuses to start, long after deployment.
RUN mkdir -p /kafka/connect/confluent-avro-converter \
 && curl -fsSL -o /tmp/avro-converter.zip "${AVRO_CONVERTER_URL}" \
 && unzip -q /tmp/avro-converter.zip -d /tmp/avro-converter \
 && find /tmp/avro-converter -name '*.jar' -exec cp {} /kafka/connect/confluent-avro-converter/ \; \
 && rm -rf /tmp/avro-converter /tmp/avro-converter.zip \
 && jars=$(find /kafka/connect/confluent-avro-converter -name '*.jar' | wc -l) \
 && test "$jars" -gt 0 \
 && echo "avro converter ${AVRO_CONVERTER_VERSION}: ${jars} jars installed"

USER kafka
