#!/bin/sh
# Write a local CA, broker PKCS12, truststore, and Kafka client.properties.
# Run before hailpath-kafka starts. Regenerates certs each time so the
# advertised host stays in the SAN. Passwords come from the environment.
set -eu

OUT=/secrets
PASS="${KAFKA_SSL_STORE_PASSWORD:?}"
USER="${KAFKA_SASL_USERNAME:-kafka}"
SASL="${KAFKA_SASL_PASSWORD:?}"
HOST="${KAFKA_ADVERTISED_HOST:-localhost}"

mkdir -p "$OUT"
cd "$OUT"

SAN="DNS:hailpath-kafka,DNS:localhost,IP:127.0.0.1"
case "$HOST" in
    *[A-Za-z]*) SAN="${SAN},DNS:${HOST}" ;;
    *) SAN="${SAN},IP:${HOST}" ;;
esac

openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
    -keyout ca.key -out ca.crt \
    -subj "/CN=hailpath-kafka-ca" >/dev/null 2>&1

openssl req -newkey rsa:2048 -nodes -keyout broker.key -out broker.csr \
    -subj "/CN=hailpath-kafka" \
    -addext "subjectAltName=${SAN}" >/dev/null 2>&1

openssl x509 -req -in broker.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out broker.crt -days 3650 -copy_extensions copy >/dev/null 2>&1

openssl pkcs12 -export -in broker.crt -inkey broker.key -out kafka.p12 \
    -name hailpath-kafka -passout "pass:${PASS}" >/dev/null 2>&1

# Java truststore must contain a trustedCertEntry. openssl pkcs12 -nokeys
# does not; keytool does.
KEYTOOL="${KEYTOOL:-/opt/java/openjdk/bin/keytool}"
rm -f trust.p12
"$KEYTOOL" -importcert -noprompt -alias ca -file ca.crt \
    -keystore trust.p12 -storetype PKCS12 -storepass "$PASS" >/dev/null 2>&1

cat > kafka.jaas <<EOF
KafkaServer {
  org.apache.kafka.common.security.plain.PlainLoginModule required
  username="${USER}"
  password="${SASL}"
  user_${USER}="${SASL}";
};
EOF

cat > client.properties <<EOF
security.protocol=SASL_SSL
sasl.mechanism=PLAIN
sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username="${USER}" password="${SASL}";
ssl.truststore.location=/etc/kafka/secrets/trust.p12
ssl.truststore.password=${PASS}
ssl.truststore.type=PKCS12
ssl.endpoint.identification.algorithm=HTTPS
EOF

chmod 644 ca.crt kafka.p12 trust.p12 client.properties broker.crt kafka.jaas
chmod 600 ca.key broker.key
chown -R 1000:1000 /secrets 2>/dev/null || true
rm -f broker.csr ca.srl
echo "kafka TLS + SASL client files written (SAN ${SAN})"
