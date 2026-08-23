#!/usr/bin/env bash
# Restart the production mosquitto broker so it re-reads ca.crl after a
# certificate revocation (P0 UC-25). Run from the repo root on the host.
#
# Usage: bash scripts/reload-broker.sh
set -euo pipefail

echo "==> Restarting fleet-mosquitto-tls to load refreshed CRL…"
docker compose --profile production restart mosquitto-tls
sleep 3
docker compose --profile production ps mosquitto-tls
