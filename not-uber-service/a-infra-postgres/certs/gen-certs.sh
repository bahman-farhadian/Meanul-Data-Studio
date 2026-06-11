#!/bin/sh
# One-shot TLS bootstrap for the nus-etcd cluster ("nus" = not-uber-service).
# Generates a CA, a shared server/peer certificate, and a client certificate
# into /certs — all valid 10 years (3650 days) — then exits. Idempotent: if
# the certs already exist, it does nothing, so restarts never rotate keys.
#
# SANs cover every etcd node name (etcd-0/1/2), localhost and 127.0.0.1, so
# the same certificate is valid for client-to-server and peer-to-peer TLS
# no matter which node a connection lands on.

set -eu

CERT_DIR=/certs
DAYS=3650
# uid/gid of the postgres user in the official postgres image — Patroni
# (running as postgres) must be able to read the client key.
PG_UID=999

if [ -f "$CERT_DIR/ca.crt" ] && [ -f "$CERT_DIR/server.crt" ] && [ -f "$CERT_DIR/client.crt" ]; then
    echo "etcd certs already present in $CERT_DIR — nothing to do"
    exit 0
fi

cd "$CERT_DIR"

cat > san.cnf <<'EOF'
[req]
distinguished_name = dn
req_extensions = ext
[dn]
[ext]
subjectAltName = @alt
extendedKeyUsage = serverAuth, clientAuth
[alt]
DNS.1 = etcd-0
DNS.2 = etcd-1
DNS.3 = etcd-2
DNS.4 = localhost
IP.1  = 127.0.0.1
EOF

cat > client.cnf <<'EOF'
[ext]
extendedKeyUsage = clientAuth
EOF

# CA
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days "$DAYS" \
    -subj "/CN=nus-etcd-ca" -out ca.crt

# Shared server/peer certificate (SANs above)
openssl genrsa -out server.key 4096
openssl req -new -key server.key -subj "/CN=nus-etcd" \
    -out server.csr -config san.cnf
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$DAYS" -sha256 -extensions ext -extfile san.cnf -out server.crt

# Client certificate (Patroni, etcdctl health checks, future components)
openssl genrsa -out client.key 4096
openssl req -new -key client.key -subj "/CN=nus-etcd-client" -out client.csr
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$DAYS" -sha256 -extensions ext -extfile client.cnf -out client.crt

rm -f server.csr client.csr san.cnf client.cnf ca.srl

chmod 0644 ca.crt server.crt client.crt
chmod 0600 ca.key server.key client.key
chown "$PG_UID:$PG_UID" client.crt client.key

echo "etcd certs generated in $CERT_DIR (valid $DAYS days)"
