# Kafka Connect with the Debezium PostgreSQL connector, plus Confluent's
# Avro converter.
#
# Why the extra step: the Debezium image ships its connectors but not
# Confluent's Avro converter, because the two are published under different
# licences. Without it, Connect could only write JSON, and the cdc.* topics
# would be encoded differently from every other topic in the stack. The
# converter is a plain set of jars, so it is added to the plugin directory
# here, at build time, rather than at runtime.

ARG CONNECT_IMAGE=quay.io/debezium/connect:3.1
FROM ${CONNECT_IMAGE}

# Keep this in step with the Schema Registry version in c-infra-kafka.
ARG AVRO_CONVERTER_VERSION=8.0.0
ARG AVRO_CONVERTER_URL=https://packages.confluent.io/api/hub/confluentinc/kafka-connect-avro-converter/versions/${AVRO_CONVERTER_VERSION}/confluentinc-kafka-connect-avro-converter-${AVRO_CONVERTER_VERSION}.zip

USER root

# unzip is not guaranteed to be in the base image. Install it if the package
# manager is there; the build only needs it for this one step.
RUN (microdnf install -y unzip && microdnf clean all) \
    || (dnf install -y unzip && dnf clean all) \
    || echo "no package manager found - relying on unzip already being present"

# The archive holds the converter and everything it needs. Only the jars are
# kept; documentation and examples are dropped.
RUN mkdir -p /kafka/connect/confluent-avro-converter \
    && curl -fsSL -o /tmp/avro-converter.zip "${AVRO_CONVERTER_URL}" \
    && unzip -q /tmp/avro-converter.zip -d /tmp/avro-converter \
    && find /tmp/avro-converter -name '*.jar' -exec cp {} /kafka/connect/confluent-avro-converter/ \; \
    && rm -rf /tmp/avro-converter /tmp/avro-converter.zip \
    && ls -1 /kafka/connect/confluent-avro-converter | head

USER kafka
