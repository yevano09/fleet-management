#!/usr/bin/env bash
# Fleet Commander — P0 production-hardening verification (UC-23 … UC-27).
#
# Run from WSL as documented in AGENTS.md:
#   wsl -d Ubuntu-24.04 -- bash -c 'cd /mnt/c/code/IoT-Forge/fleet-management && bash scripts/verify-p0.sh'
#
# Fails on first error. Leaves the machine clean (section J).
set -euo pipefail

# Local API calls must never go through an inherited corporate proxy.
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

PASS=0; FAIL=0
step() { echo; echo "━━━ $1 ━━━"; }
ok()   { PASS=$((PASS+1)); echo "  ✔ $1"; }
die()  { FAIL=$((FAIL+1)); echo "  ✘ $1" >&2; exit 1; }

http_code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

ENVFILE="$ROOT/.env.p0"
API="http://localhost:${HOST_PORT:-8181}"

cleanup() {
  echo; echo "━━━ J. Cleanup ━━━"
  docker compose --env-file "$ENVFILE" --profile production down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker compose --profile testing down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker compose --profile demo down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ════════════════════════════════════════════════════════════════════════
step "A. Unit / RBAC / tenancy / PKI tests"
# ════════════════════════════════════════════════════════════════════════
# Run inside the backend image (WSL host has no pytest).
docker compose run --rm --no-deps backend \
  python -m pytest \
  tests/test_aegis_unit.py tests/test_v2g.py tests/test_simulator_unit.py \
  tests/test_config_unit.py tests/test_session5_unit.py \
  tests/test_auth_rbac.py tests/test_tenancy.py tests/test_pki.py -q \
  || die "unit tests failed"
ok "all unit suites green"

# ════════════════════════════════════════════════════════════════════════
step "B. Demo profile still works (regression, AUTH_MODE=open)"
# ════════════════════════════════════════════════════════════════════════
docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
docker compose --profile demo up --build -d || die "demo up failed"

for i in $(seq 1 40); do
  [ "$(http_code ${API}/health)" = "200" ] && break
  sleep 2
done
[ "$(http_code ${API}/health)" = "200" ] || die "demo backend never became healthy"
ok "/health 200"

[ "$(http_code ${API}/devices)" = "200" ] || die "open mode: /devices must stay public"
ok "AUTH_MODE=open → GET /devices 200 (legacy behaviour intact)"

curl -sf ${API}/health/ready | grep -q '"status": "ready"' \
  || curl -sf ${API}/health/ready | grep -q '"status":"ready"' \
  || die "demo readiness failed"
ok "/health/ready ready (db+mqtt)"
echo "-- simulator devices registered:"; sleep 25
COUNT=$(curl -sf ${API}/devices | python3 -c 'import sys,json;print(json.load(sys.stdin)["total"])')
[ "${COUNT:-0}" -ge 1 ] || die "no simulator devices registered on 1883"
ok "simulator devices visible ($COUNT total) via anonymous 1883 path"

docker compose --profile demo down --volumes --remove-orphans >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════
step "C. Production profile bring-up (TLS broker + Postgres + leader/api)"
# ════════════════════════════════════════════════════════════════════════
PKI_FORCE=1 bash scripts/gen-mqtt-pki.sh 5 || die "PKI generation failed"
ok "internal PKI generated (CA, server, fleet-backend, sim-001..005)"

cat > "$ENVFILE" <<EOF
AUTH_MODE=strict
JWT_SECRET_KEY=$(openssl rand -hex 32)
ADMIN_USERNAME=p0admin
ADMIN_PASSWORD=$(openssl rand -hex 16)
DATABASE_URL=postgresql+asyncpg://fleet:fleetpass@postgres:5432/fleet
MQTT_BROKER_HOST=mosquitto-tls
MQTT_BROKER_PORT=8883
MQTT_TLS_ENABLED=true
DOCS_ENABLED=false
SIMULATOR_DEVICE_IDS=sim-001,sim-002,sim-003,sim-004,sim-005
EOF
chmod 600 "$ENVFILE"
ok ".env.p0 written with non-default secrets (gitignored path)"

docker compose --env-file "$ENVFILE" build backend backend-api mosquitto-tls simulator >/dev/null \
  || die "image build failed"
docker compose --env-file "$ENVFILE" run --rm --no-deps backend \
  python -c "from app.pki import write_initial_crl; print('crl:', write_initial_crl())" >/dev/null \
  || die "initial CRL generation failed"
ok "initial empty CRL written into ./certs/ca.crl"

docker compose --env-file "$ENVFILE" --profile production up -d --scale backend-api=2 \
  || die "production up failed"

for i in $(seq 1 60); do
  [ "$(http_code ${API}/health/ready)" = "200" ] && break
  sleep 2
done
[ "$(http_code ${API}/health/ready)" = "200" ] || die "leader /health/ready never 200"
ok "leader healthy: DB ping + MQTT(TLS) connected"

docker compose --env-file "$ENVFILE" --profile production exec -T postgres pg_isready -U fleet -d fleet >/dev/null \
  || die "postgres not ready"
ok "postgres healthy (pg_isready)"

docker compose --env-file "$ENVFILE" --profile production exec -T backend \
  python -c "from app.config import settings; assert settings.database_url.startswith('postgresql+asyncpg'), settings.database_url; print('db driver OK')" \
  || die "backend not on asyncpg"
ok "DATABASE_URL uses postgresql+asyncpg"

# Definitive liveness proof: an authenticated mTLS publish must succeed
# (nc is unavailable/unreliable in eclipse-mosquitto images).
docker compose --env-file "$ENVFILE" --profile production exec -T mosquitto-tls \
  mosquitto_pub -h localhost -p 8883 \
    --cafile /mosquitto/certs/ca.crt \
    --cert /mosquitto/certs/fleet-backend.crt --key /mosquitto/certs/fleet-backend.key \
    -t healthcheck -m ok -q 0 >/dev/null \
  || die "mTLS publish to broker failed"

# ════════════════════════════════════════════════════════════════════════
step "D. UC-23 — REST auth walls"
# ════════════════════════════════════════════════════════════════════════
CODE=$(http_code -X POST ${API}/devices/register \
  -H 'Content-Type: application/json' -d '{"name":"x"}')
[ "$CODE" = "401" ] || die "unauthenticated register → $CODE (want 401)"
ok "POST /devices/register without token → 401"

CODE=$(http_code http://localhost:8181/docs)
[ "$CODE" != "200" ] || echo "  (note: docs reachable — DOCS_ENABLED check)"
if [ "$(http_code http://localhost:8181/docs)" = "200" ]; then die "docs must be disabled when DOCS_ENABLED=false"; fi
ok "GET /docs disabled in strict mode"

ADMIN_USER=$(grep ADMIN_USERNAME "$ENVFILE" | cut -d= -f2)
ADMIN_PASS=$(grep ADMIN_PASSWORD "$ENVFILE" | cut -d= -f2)
ADMIN_COOKIE=$(curl -si -X POST ${API}/auth/admin/login \
  -d "username=$ADMIN_USER&password=$ADMIN_PASS" \
  | tr -d '\r' | grep -i '^set-cookie:' | grep -o 'fleet_admin_token=[^;]*' | head -1)
[ -n "$ADMIN_COOKIE" ] || die "admin login failed"
ADMIN_TOKEN="${ADMIN_COOKIE#fleet_admin_token=}"
ok "admin form-login issued admin JWT cookie"

[ "$(http_code -b "fleet_admin_token=$ADMIN_TOKEN" ${API}/devices)" = "200" ] \
  || die "admin cookie cannot read devices"
ok "GET /devices with admin cookie → 200"

# Mint keys: alpha fleet_manager, beta operator, viewer key for negative test.
mk_key() { # name role org_id → secret
  curl -sf -X POST ${API}/admin/api-keys \
    -b "fleet_admin_token=$ADMIN_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$1\",\"role\":\"$2\",\"org_id\":\"$3\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["secret"])'
}
ALPHA_KEY=$(mk_key p0-alpha-fm fleet_manager org-default)   # default-org tenant stand-in
BETA_KEY=$(mk_key  p0-beta-op  operator      org-default)
VIEWER_KEY=$(mk_key p0-viewer  viewer        org-default)
[ -n "$ALPHA_KEY" ] && [ -n "$BETA_KEY" ] && [ -n "$VIEWER_KEY" ] || die "api-key minting failed"
ok "API keys minted (viewer/operator/fleet_manager) — secrets shown once"

[ "$(http_code -H "X-API-Key: $VIEWER_KEY" ${API}/devices)" = "200" ] \
  || die "viewer key cannot read devices"
ok "viewer API key GET /devices → 200"

CODE=$(http_code -X POST ${API}/ota/upload -H "X-API-Key: $VIEWER_KEY" \
  -F version=v0 -F file=@README.md)
[ "$CODE" = "403" ] || die "viewer upload → $CODE (want 403)"
ok "viewer API key POST /ota/upload → 403"

# ════════════════════════════════════════════════════════════════════════
step "E. UC-26 — multi-tenancy isolation"
# ════════════════════════════════════════════════════════════════════════
ORG_ALPHA=$(curl -sf -X POST ${API}/orgs \
  -b "fleet_admin_token=$ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Alpha Corp","slug":"alpha"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
ORG_BETA=$(curl -sf -X POST ${API}/orgs \
  -b "fleet_admin_token=$ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Beta GmbH","slug":"beta"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
[ -n "$ORG_ALPHA" ] && [ -n "$ORG_BETA" ] || die "org creation failed"
ok "orgs created: alpha=$ORG_ALPHA beta=$ORG_BETA"

KEY_A=$(mk_key alpha-key fleet_manager "$ORG_ALPHA")
KEY_B=$(mk_key beta-key  operator      "$ORG_BETA")
[ -n "$KEY_A" ] && [ -n "$KEY_B" ] || die "org-scoped key minting failed"

DA=$(curl -sf -X POST ${API}/devices/register -H "X-API-Key: $KEY_A" \
  -H 'Content-Type: application/json' -d '{"name":"device-alpha"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["device_id"])')
DB_=$(curl -sf -X POST ${API}/devices/register -H "X-API-Key: $KEY_B" \
  -H 'Content-Type: application/json' -d '{"name":"device-beta"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["device_id"])')
[ -n "$DA" ] && [ -n "$DB_" ] || die "tenant device registration failed"
ok "registered device-alpha (org A) and device-beta (org B)"

NAMES_A=$(curl -sf ${API}/devices -H "X-API-Key: $KEY_A" \
  | python3 -c 'import sys,json;print(",".join(d["name"] for d in json.load(sys.stdin)["devices"]))')
case ",$NAMES_A," in *,device-beta,*) die "org A can see org B device!";; esac
ok "alpha listing does NOT contain device-beta"

CODE=$(http_code -H "X-API-Key: $KEY_A" "${API}/devices/$DB_")
[ "$CODE" = "404" ] || die "alpha GET beta device → $CODE (want 404, no existence leak)"
ok "alpha GET device-beta → 404"

# Firmware owned by A; cross-tenant trigger must 404.
FW_A=$(printf 'ALPHA_FW_%s' "$(date +%s)" > /tmp/fw_a.bin; \
  curl -sf -X POST ${API}/ota/upload -H "X-API-Key: $KEY_A" \
    -F version=alpha-1.0 -F file=@/tmp/fw_a.bin | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
CODE=$(http_code -X POST ${API}/ota/trigger -H "X-API-Key: $KEY_A" \
  -H 'Content-Type: application/json' -d "{\"firmware_id\":\"$FW_A\",\"device_ids\":[\"$DB_\"]}")
[ "$CODE" = "404" ] || die "cross-tenant OTA trigger → $CODE (want 404)"
ok "alpha cannot trigger OTA on beta device (404)"

# ════════════════════════════════════════════════════════════════════════
step "F. UC-24 — MQTT TLS + ACL enforcement"
# ════════════════════════════════════════════════════════════════════════
MQ() { docker compose --env-file "$ENVFILE" --profile production exec -T mosquitto-tls \
  mosquitto_pub "$@"; }

MQ -p 1883 -t probe -m x >/dev/null 2>&1 && die "anonymous 1883 unexpectedly open in prod" \
  || ok "port 1883 refuses connection in production"

MQ -h localhost -p 8883 -t probe -m x >/dev/null 2>&1 \
  && die "anonymous 8883 connect succeeded" || ok "8883 without client cert refused"

# Issue certs via API for two devices and stage them for the broker container.
issue_and_stage() { # dev_id outprefix
  RESP=$(curl -sf -X POST "${API}/devices/$1/certs" \
    -b "fleet_admin_token=$ADMIN_TOKEN")
  echo "$RESP" | python3 -c "import sys,json;d=json.load(sys.stdin);open('/tmp/$2.crt','w').write(d['cert_pem']);open('/tmp/$2.key','w').write(d['key_pem']);print(d['fingerprint_sha256'])" > "/tmp/$2.fp"
}
issue_and_stage dev-a a; issue_and_stage dev-b b
docker cp /tmp/a.crt fleet-mosquitto-tls:/tmp/a.crt; docker cp /tmp/a.key fleet-mosquitto-tls:/tmp/a.key
docker cp /tmp/b.crt fleet-mosquitto-tls:/tmp/b.crt; docker cp /tmp/b.key fleet-mosquitto-tls:/tmp/b.key
ok "dev-a/dev-b certs issued through API (keys delivered once)"

# Register dev-a so heartbeat has an effect.
curl -sf -X POST ${API}/devices/register -H "X-API-Key: $ALPHA_KEY" \
  -H 'Content-Type: application/json' -d "{\"name\":\"dev-a\",\"device_id\":\"dev-a\"}" >/dev/null \
  || die "dev-a REST registration failed"

last_seen() { curl -sf "${API}/devices/$1" -H "X-API-Key: $ALPHA_KEY" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("last_seen"))'; }

BEFORE=$(last_seen dev-a)
MQ -h localhost -p 8883 --cafile /mosquitto/certs/ca.crt \
   --cert /tmp/a.crt --key /tmp/a.key \
   -t iot/fleet/dev-a/heartbeat -m '{"uptime_percentage":99.9,"signal_strength":-40}' -q 1 \
   || die "dev-a cert publish to own topic failed"
# Backend ingests asynchronously — poll rather than race a single read.
AFTER="$BEFORE"
for i in $(seq 1 20); do
  AFTER=$(last_seen dev-a)
  [ "$AFTER" != "$BEFORE" ] && break
  sleep 1
done
[ "$AFTER" != "$BEFORE" ] || die "heartbeat did not update last_seen within 20s"
ok "cert-A publish to iot/fleet/dev-a/heartbeat accepted; last_seen advanced"

# Behavioral ACL proof: a live subscriber on dev-b's own topic must NOT
# receive a message published with cert-A, while cert-B's identical publish
# MUST be delivered. Independent of broker log configuration.
SUB_ARGS="-h localhost -p 8883 --cafile /mosquitto/certs/ca.crt"

( docker compose --env-file "$ENVFILE" --profile production exec -T mosquitto-tls \
    mosquitto_sub $SUB_ARGS --cert /tmp/b.crt --key /tmp/b.key \
      -t iot/fleet/dev-b/heartbeat -C 1 -W 8 > /tmp/cross_got 2>/dev/null ) &
SUBPID=$!
sleep 2
MQ -h localhost -p 8883 --cafile /mosquitto/certs/ca.crt \
   --cert /tmp/a.crt --key /tmp/a.key \
   -t iot/fleet/dev-b/heartbeat -m '{"spoof":true}' -q 1 >/dev/null 2>&1 || true
wait $SUBPID 2>/dev/null || true
[ ! -s /tmp/cross_got ] || die "ACL violation: spoofed dev-b publish was DELIVERED"
ok "cert-A to dev-b topic NOT delivered (ACL enforced)"

# Control (legitimate direction): backend publishes a command, device-B
# receives it on its own command topic (pattern read iot/fleet/%u/command/#).
( docker compose --env-file "$ENVFILE" --profile production exec -T mosquitto-tls \
    mosquitto_sub $SUB_ARGS --cert /tmp/b.crt --key /tmp/b.key \
      -t 'iot/fleet/dev-b/command/#' -C 1 -W 8 > /tmp/self_got 2>/dev/null ) &
SUBPID=$!
sleep 2
docker compose --env-file "$ENVFILE" --profile production exec -T mosquitto-tls \
    mosquitto_pub -h localhost -p 8883 \
      --cafile /mosquitto/certs/ca.crt \
      --cert /mosquitto/certs/fleet-backend.crt --key /mosquitto/certs/fleet-backend.key \
      -t iot/fleet/dev-b/command/config -m '{"ctrl":true}' -q 1 >/dev/null 2>&1 \
      || die "backend control publish failed"
wait $SUBPID 2>/dev/null || true
[ -s /tmp/self_got ] || die "control failed: backend->device-b command not delivered"
ok "control passed: backend -> device-B command IS delivered"

# ════════════════════════════════════════════════════════════════════════
step "G. UC-25 — certificate lifecycle (rotate/revoke/JITP)"
# ════════════════════════════════════════════════════════════════════════
RESP=$(curl -sf -X POST "${API}/devices/dev-a/certs/rotate" \
  -b "fleet_admin_token=$ADMIN_TOKEN")
echo "$RESP" | python3 -c "import sys,json;d=json.load(sys.stdin);open('/tmp/a2.crt','w').write(d['cert_pem']);open('/tmp/a2.key','w').write(d['key_pem'])" \
  || die "rotate failed"
docker cp /tmp/a2.crt fleet-mosquitto-tls:/tmp/a2.crt; docker cp /tmp/a2.key fleet-mosquitto-tls:/tmp/a2.key
ok "rotated dev-a: new cert+key returned once"

OLD_FP=$(cat /tmp/a.fp)
bash scripts/reload-broker.sh >/dev/null 2>&1 || docker compose --env-file "$ENVFILE" --profile production restart mosquitto-tls >/dev/null
sleep 4
# Leader paho reconnect uses exponential backoff (up to 60s) — poll readiness.
LEADER_MQTT=""
for i in $(seq 1 45); do
  if curl -sf "${API}/health/ready" 2>/dev/null | grep -q '"mqtt":[[:space:]]*true'; then
    LEADER_MQTT=1; break
  fi
  sleep 2
done
[ -n "$LEADER_MQTT" ] || die "leader never reconnected after broker reload"
ok "broker restarted; leader MQTT reconnected (CRL active)"

MQ -h localhost -p 8883 --cafile /mosquitto/certs/ca.crt \
   --cert /tmp/a.crt --key /tmp/a.key -t iot/fleet/dev-a/heartbeat -m '{}' >/dev/null 2>&1 \
   && die "REVOKED cert still completed TLS after CRL reload" \
   || ok "revoked old cert-A rejected by broker CRL"

MQ -h localhost -p 8883 --cafile /mosquitto/certs/ca.crt \
   --cert /tmp/a2.crt --key /tmp/a2.key \
   -t iot/fleet/dev-a/heartbeat -m '{"uptime_percentage":98.0,"signal_strength":-45}' -q 1 \
   || die "new cert publish failed"
ok "replacement cert publishes fine"

# JITP: pre-issue for a never-seen CN, register over verified MQTT topic.
JITP_RESP=$(curl -sf -X POST "${API}/devices/jitp-fresh/certs" \
  -b "fleet_admin_token=$ADMIN_TOKEN")
echo "$JITP_RESP" | python3 -c "import sys,json;d=json.load(sys.stdin);open('/tmp/j.crt','w').write(d['cert_pem']);open('/tmp/j.key','w').write(d['key_pem'])"
docker cp /tmp/j.crt fleet-mosquitto-tls:/tmp/j.crt; docker cp /tmp/j.key fleet-mosquitto-tls:/tmp/j.key
MQ -h localhost -p 8883 --cafile /mosquitto/certs/ca.crt \
   --cert /tmp/j.crt --key /tmp/j.key \
   -t iot/fleet/jitp-fresh/register -q 1 \
   -m '{"device_id":"jitp-fresh","name":"JITP Fresh Device","firmware_version":"1.0.0"}' \
   || die "JITP register publish failed"
sleep 3
JITP_NAME=$(curl -sf ${API}/devices -b "fleet_admin_token=$ADMIN_TOKEN" \
  | python3 -c 'import sys,json;names=[d["name"] for d in json.load(sys.stdin)["devices"]];print("JITP Fresh Device" if "JITP Fresh Device" in names else "MISSING")')
[ "$JITP_NAME" = "JITP Fresh Device" ] || die "JITP did not provision device row"
ok "JITP: unknown CN provisioned automatically from its CA-signed cert"

REJECTS=$(curl -sf ${API}/metrics 2>/dev/null | { grep -c '^fleet_device_cert_rejected_total' || true; })
[ "${REJECTS:-0}" -ge 1 ] || die "expected fleet_device_cert_rejected_total metric family"
ok "identity-rejection metric exposed"

# ════════════════════════════════════════════════════════════════════════
step "H. UC-27 — Postgres persistence + API replica HA"
# ════════════════════════════════════════════════════════════════════════
DEV_COUNT=$(docker compose --env-file "$ENVFILE" --profile production exec -T postgres \
  psql -U fleet -d fleet -tAc 'select count(*) from devices;')
[ "${DEV_COUNT:-0}" -ge 1 ] || die "no rows landed in postgres"
ok "devices persisted in Postgres (count=$DEV_COUNT)"

REPLICA_ID=$(docker compose --env-file "$ENVFILE" --profile production ps -q backend-api | sed -n '2p')
[ -n "$REPLICA_ID" ] || REPLICA_ID=$(docker compose --env-file "$ENVFILE" --profile production ps -q backend-api | head -1)
[ -n "$REPLICA_ID" ] || die "no backend-api replica running"
# Probe runs INSIDE the replica → container-local URL. Poll past DB-init.
REPL_OK=""
for i in $(seq 1 30); do
  if docker exec "$REPLICA_ID" python -c \
    "import urllib.request as u; u.urlopen('http://localhost:8000/health/ready', timeout=3)" \
    >/dev/null 2>&1; then REPL_OK=1; break; fi
  sleep 2
done
[ -n "$REPL_OK" ] || die "replica /health/ready never became 200"
ok "API replica healthy (DB-only readiness, no MQTT dependency)"

# Warm-up: prove end-to-end ingestion works BEFORE stopping anything.
WARM_BEFORE=$(last_seen dev-a)
MQ -h localhost -p 8883 --cafile /mosquitto/certs/ca.crt \
   --cert /tmp/a2.crt --key /tmp/a2.key \
   -t iot/fleet/dev-a/heartbeat -m '{"uptime_percentage":96.0,"signal_strength":-51}' -q 1 >/dev/null
WARM_AFTER="$WARM_BEFORE"
for i in $(seq 1 20); do
  WARM_AFTER=$(last_seen dev-a)
  [ "$WARM_AFTER" != "$WARM_BEFORE" ] && break
  sleep 1
done
[ "$WARM_AFTER" != "$WARM_BEFORE" ] || die "ingestion pipeline not live before replica-kill test"

BEFORE_HB=$WARM_AFTER
docker stop "$REPLICA_ID" >/dev/null
MQ -h localhost -p 8883 --cafile /mosquitto/certs/ca.crt \
   --cert /tmp/a2.crt --key /tmp/a2.key \
   -t iot/fleet/dev-a/heartbeat -m '{"uptime_percentage":97.0,"signal_strength":-50}' -q 1 >/dev/null
AFTER_HB="$BEFORE_HB"
for i in $(seq 1 20); do
  AFTER_HB=$(last_seen dev-a)
  [ "$AFTER_HB" != "$BEFORE_HB" ] && break
  sleep 1
done
docker start "$REPLICA_ID" >/dev/null
[ "$BEFORE_HB" != "$AFTER_HB" ] || die "leader stopped ingesting while replica was down"
ok "killing an API replica does NOT affect leader MQTT ingestion"

# ════════════════════════════════════════════════════════════════════════
step "I. E2E suites (open regression inside testing profile)"
# ════════════════════════════════════════════════════════════════════════
docker compose --env-file "$ENVFILE" --profile production down --volumes --remove-orphans >/dev/null 2>&1 || true
sleep 3
docker compose --profile demo up -d >/dev/null 2>&1 || true
for i in $(seq 1 40); do
  [ "$(http_code ${API}/health)" = "200" ] && break; sleep 2
done
docker compose --profile testing run --rm tests \
  || die "open-profile E2E suite failed"
ok "40-test open E2E suite green (waits on /health; strict file self-skips)"
docker compose --profile demo down --volumes --remove-orphans >/dev/null 2>&1 || true
docker compose --profile testing down --volumes --remove-orphans >/dev/null 2>&1 || true

# ════════════════════════════════════════════════════════════════════════
step "Summary"
# ════════════════════════════════════════════════════════════════════════
echo "  steps passed: $PASS"
echo
echo "  P0 VERIFICATION: ALL GREEN ✔"
