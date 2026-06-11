#!/bin/sh
# One-shot TLS bootstrap for the nus-etcd cluster ("nus" = not-uber-service).
# Run it with:  docker compose run --rm etcd-certgen   (removes itself)
#
# Generates a CA, a shared server/peer certificate, and a client certificate
# into /certs — all valid 10 years (3650 days) — then exits. Idempotent: if
# the certs already exist, it does nothing, so re-runs never rotate keys.
#
# Extension notes (do not slim these down):
# - the CA carries explicit basicConstraints + keyUsage (keyCertSign) and
#   SKID/AKID — Python 3.13's ssl module verifies in strict X.509 mode and
#   REJECTS CAs without a keyUsage extension (Patroni would fail with
#   "CA cert does not include key usage extension");
# - the server/peer certificate carries SANs for every etcd node name
#   (etcd-0/1/2), localhost and 127.0.0.1, so the same certificate is valid
#   for client-to-server and peer-to-peer TLS on every node.

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

cat > ca.cnf <<'EOF'
[req]
distinguished_name = dn
x509_extensions = ca_ext
prompt = no
[dn]
CN = nus-etcd-ca
[ca_ext]
basicConstraints = critical, CA:TRUE
keyUsage = critical, digitalSignature, cRLSign, keyCertSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always, issuer
EOF

# NOTE: the [ext] sections below are applied at SIGNING time (-extfile),
# not at CSR time — authorityKeyIdentifier needs the issuer certificate,
# which only exists when the CA signs.
cat > server.cnf <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = nus-etcd
[ext]
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid, issuer
subjectAltName = @alt
[alt]
DNS.1 = etcd-0
DNS.2 = etcd-1
DNS.3 = etcd-2
DNS.4 = localhost
IP.1  = 127.0.0.1
EOF

cat > client.cnf <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = nus-etcd-client
[ext]
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid, issuer
EOF

# CA
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days "$DAYS" \
    -config ca.cnf -out ca.crt

# Shared server/peer certificate (SANs above)
openssl genrsa -out server.key 4096
openssl req -new -key server.key -config server.cnf -out server.csr
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$DAYS" -sha256 -extensions ext -extfile server.cnf -out server.crt

# Client certificate (Patroni, etcdctl health checks, future components)
openssl genrsa -out client.key 4096
openssl req -new -key client.key -config client.cnf -out client.csr
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$DAYS" -sha256 -extensions ext -extfile client.cnf -out client.crt

rm -f server.csr client.csr ca.cnf server.cnf client.cnf ca.srl

chmod 0644 ca.crt server.crt client.crt
chmod 0600 ca.key server.key client.key
chown "$PG_UID:$PG_UID" client.crt client.key

echo "etcd certs generated in $CERT_DIR (valid $DAYS days)"

# quick self-verification — fail loudly here rather than at etcd startup
openssl verify -CAfile ca.crt server.crt client.crt
