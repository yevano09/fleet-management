#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# Fleet Commander — Automated Demo Pitch Script
#
# Walks through 12 key features with color-coded narration and live API calls.
# Requires the Docker stack to be running (docker compose --profile demo up -d).
#
# Usage:
#   ./demo_pitch.sh              # Full demo
#   ./demo_pitch.sh --quick      # Quick demo (shorter waits)
#   ./demo_pitch.sh --no-v2g     # Skip V2G section
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────
BASE_URL="${FLEET_URL:-http://localhost:8181}"
QUICK=false
SKIP_V2G=false

for arg in "$@"; do
  case $arg in
    --quick)    QUICK=true; shift ;;
    --no-v2g)   SKIP_V2G=true; shift ;;
    *)          ;;
  esac
done

# Wait durations (shorter in quick mode)
if $QUICK; then
  WAIT_BEAT=1; WAIT_OTA=3; WAIT_LONG=5
else
  WAIT_BEAT=2; WAIT_OTA=5; WAIT_LONG=8
fi

# ── Color helpers ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD='\033[1m'; TEAL='\033[36m'; GREEN='\033[32m'; AMBER='\033[33m'
  RED='\033[31m'; PURPLE='\033[35m'; BLUE='\033[34m'; RESET='\033[0m'
else
  BOLD=''; TEAL=''; GREEN=''; AMBER=''; RED=''; PURPLE=''; BLUE=''; RESET=''
fi

header() {
  echo ""
  echo -e "${BOLD}${TEAL}═══════════════════════════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}${TEAL}  $1${RESET}"
  echo -e "${BOLD}${TEAL}═══════════════════════════════════════════════════════════════════${RESET}"
}

narrate() {
  echo -e "${BOLD}${BLUE}  ▸ $1${RESET}"
}

success() {
  echo -e "  ${GREEN}✓ $1${RESET}"
}

warn() {
  echo -e "  ${AMBER}! $1${RESET}"
}

info() {
  echo -e "  ${PURPLE}$1${RESET}"
}

wait_for() {
  echo -e "  ${BLUE}... waiting ${1}s for ${2}...${RESET}"
  sleep "$1"
}

# ── Helper functions ────────────────────────────────────────────────────────
api_get() {
  curl -s "${BASE_URL}$1" 2>/dev/null
}

api_post() {
  curl -s -X POST "${BASE_URL}$1" \
    -H "Content-Type: application/json" \
    -d "$2" 2>/dev/null
}

api_post_form() {
  curl -s -X POST "${BASE_URL}$1" -F "$2" -F "$3" 2>/dev/null
}

check_backend() {
  if ! curl -s "${BASE_URL}/devices" > /dev/null 2>&1; then
    echo -e "${RED}ERROR: Backend not reachable at ${BASE_URL}${RESET}"
    echo -e "${AMBER}Start the stack first: docker compose --profile demo up --build -d${RESET}"
    exit 1
  fi
}

# ── Pre-flight check ────────────────────────────────────────────────────────
check_backend
echo -e "${BOLD}${GREEN}"
echo "  ╔═══════════════════════════════════════════════════════════════╗"
echo "  ║         FLEET COMMANDER — LIVE DEMO PITCH                    ║"
echo "  ║         IoT Device Management at Production Scale            ║"
echo "  ╚═══════════════════════════════════════════════════════════════╝"
echo -e "${RESET}"
narrate "Backend: ${BASE_URL}"
narrate "Dashboard: ${BASE_URL}/"
narrate "API Docs: ${BASE_URL}/docs"
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 1: Fleet Overview
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 1: Fleet Overview"
narrate "Let's see what's in the fleet right now..."
DEVICES=$(api_get "/devices" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Total: {d[\"total\"]}'); [print(f'  {x[\"name\"]:20s} fw={x[\"firmware_version\"]:12s} status={x[\"status\"]:8s} signal={x.get(\"signal_strength\",\"?\")}dBm city={x.get(\"city\",\"?\")}') for x in d['devices'][:10]]" 2>/dev/null || echo "  (parsing failed, raw response above)")
echo "$DEVICES"
success "Devices auto-registered via MQTT on first connect — no manual setup needed"
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 2: Telemetry Time-Series (Feature 1)
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 2: Telemetry Time-Series + Trend Charts"
narrate "Every heartbeat is recorded as a telemetry data point for trend analysis..."
DEVICE_ID=$(api_get "/devices" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['devices'][0]['id'])" 2>/dev/null)
if [ -n "$DEVICE_ID" ]; then
  TELEMETRY=$(api_get "/telemetry/${DEVICE_ID}?hours=1&limit=5" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Samples: {d[\"total\"]}'); [print(f'  {p[\"timestamp\"][:19]}  sig={p.get(\"signal_strength\",\"?\")}  temp={p.get(\"temperature\",\"?\")}  cpu={p.get(\"cpu_usage\",\"?\")}') for p in d['points'][-5:]]" 2>/dev/null || echo "  (no telemetry yet)")
  echo "$TELEMETRY"
  success "Telemetry enables predictive maintenance and trend visualization (Chart.js on dashboard)"
fi
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 3: Firmware Upload with Signing (Feature 8)
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 3: Firmware Upload + Cryptographic Signing"
narrate "Uploading firmware v2.1.0 with Ed25519 signing..."
FW_CONTENT="FLEET_COMMANDER_V2_1_DEMO_BINARY_$(date +%s)"
FW_RESP=$(api_post_form "/ota/upload" "version=2.1.0-demo" "file=@-;filename=fw_v2.1.bin;type=application/octet-stream" <<< "$FW_CONTENT" 2>/dev/null || echo '{"error":"upload failed"}')
FW_ID=$(echo "$FW_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -n "$FW_ID" ]; then
  echo "$FW_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Firmware: {d.get(\"version\",\"?\")}  SHA256: {d.get(\"sha256_hash\",\"?\")[:16]}...  Size: {d.get(\"file_size\",0)}B  Signed: {bool(d.get(\"signature\"))}')" 2>/dev/null
  success "Firmware uploaded with SHA256 hash + optional Ed25519 signature"
else
  warn "Firmware upload may have failed (version may already exist)"
fi
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 4: OTA Trigger + Rollback
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 4: OTA Update + Automatic Rollback"
narrate "Triggering OTA for all online devices. 20% will fail and auto-rollback..."
if [ -n "$FW_ID" ]; then
  OTA_RESP=$(api_post "/ota/trigger" "{\"firmware_id\": \"${FW_ID}\", \"all_devices\": true}" 2>/dev/null)
  echo "$OTA_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Triggered: {d.get(\"message\",\"?\")}  Deployments: {len(d.get(\"deployment_ids\",[]))}  MQTT failures: {len(d.get(\"mqtt_failures\",[]))}')" 2>/dev/null
  wait_for $WAIT_OTA "OTA to complete"
  OTA_STATUS=$(api_get "/ota/status" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Success: {d[\"success_count\"]}  Failed: {d[\"failed_count\"]}  In-progress: {d[\"in_progress_count\"]}')" 2>/dev/null)
  info "Status: $OTA_STATUS"
  success "20% failure rate → hash_mismatch → automatic rollback to previous firmware"
fi
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 5: Scheduled OTA with Maintenance Windows (Feature 4)
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 5: Scheduled OTA / Maintenance Windows"
narrate "Scheduling an OTA campaign for 2am with 9am-5pm blackout..."
SCHED_TIME=$(python3 -c "from datetime import datetime,timedelta; print((datetime.utcnow()+timedelta(hours=6)).isoformat())" 2>/dev/null)
if [ -n "$FW_ID" ] && [ -n "$SCHED_TIME" ]; then
  SCHED_RESP=$(api_post "/ota/schedules" "{\"name\":\"Demo Nightly Update\",\"firmware_id\":\"${FW_ID}\",\"all_devices\":true,\"scheduled_for\":\"${SCHED_TIME}\",\"blackout_start_hour\":9,\"blackout_end_hour\":17,\"canary_percent\":10}" 2>/dev/null)
  echo "$SCHED_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Schedule: {d.get(\"name\",\"?\")}  Status: {d.get(\"status\",\"?\")}  Canary: {d.get(\"canary_percent\",0)}%  Blackout: {d.get(\"blackout_start_hour\",\"?\")}-{d.get(\"blackout_end_hour\",\"?\")}h')" 2>/dev/null
  success "OTA campaigns can be scheduled for off-peak hours with blackout windows"
fi
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 6: Geofencing (Feature 2)
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 6: Geofencing & Geo-alerts"
narrate "Creating a 5km geofence around Bangalore depot..."
GEO_RESP=$(api_post "/geofences" '{"name":"Bangalore Depot","shape":"circle","center_lat":12.9716,"center_lng":77.5946,"radius_meters":5000,"alert_on_enter":true,"alert_on_exit":true,"color":"#2DD4BF"}' 2>/dev/null)
GEO_ID=$(echo "$GEO_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -n "$GEO_ID" ]; then
  echo "$GEO_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Geofence: {d.get(\"name\",\"?\")}  Shape: {d.get(\"shape\",\"?\")}  Radius: {d.get(\"radius_meters\",0)}m')" 2>/dev/null
  success "Devices entering/exiting geofences trigger alerts + map overlay on dashboard"
fi
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 7: Predictive Maintenance (Feature 3)
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 7: Predictive Maintenance Agent"
narrate "Running AI-powered predictive analysis on telemetry trends..."
PRED_RESP=$(api_get "/agents/predictive-scan" 2>/dev/null)
echo "$PRED_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Predictions: {d.get(\"predictions_count\",0)}'); [print(f'  → {p[\"risk_type\"]}: {p[\"risk_score\"]*100:.0f}% risk (device {p[\"device_id\"][:8]}...)') for p in (d.get('details',{}).get('predictions',[]))[:5]]" 2>/dev/null
success "Linear regression on telemetry trends predicts failures BEFORE they happen"
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 8: Device Shadow / Digital Twin (Feature 7)
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 8: Device Shadow / Digital Twin"
narrate "Updating desired shadow state for a device..."
if [ -n "$DEVICE_ID" ]; then
  SHADOW_RESP=$(curl -s -X PUT "${BASE_URL}/shadow/${DEVICE_ID}" -H "Content-Type: application/json" -d '{"state":"desired","payload":{"heartbeat_interval":15,"log_level":"DEBUG","ota_poll_interval":30}}' 2>/dev/null)
  echo "$SHADOW_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Shadow: state={d.get(\"state\",\"?\")}  version={d.get(\"version\",0)}')" 2>/dev/null
  success "Desired state pushed via MQTT; devices report back → reported state (AWS IoT pattern)"
fi
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 9: Offline Command Queue (Feature 5)
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 9: Offline Command Queue"
narrate "Queuing a command for a device — delivered on reconnect..."
if [ -n "$DEVICE_ID" ]; then
  CMD_RESP=$(api_post "/commands/queue" "{\"device_id\":\"${DEVICE_ID}\",\"command_type\":\"config\",\"payload\":{\"heartbeat_interval_seconds\":5},\"ttl_seconds\":3600}" 2>/dev/null)
  echo "$CMD_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Command: type={d.get(\"command_type\",\"?\")}  status={d.get(\"status\",\"?\")}  TTL: 3600s')" 2>/dev/null
  success "Commands buffered for offline devices; auto-delivered when they reconnect"
fi
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 10: Audit Log (Feature 6)
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 10: Audit Log — Full Traceability"
narrate "Every action we just did was recorded in the audit log..."
AUDIT_RESP=$(api_get "/audit?limit=10" 2>/dev/null)
echo "$AUDIT_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Total audit entries: {d[\"total\"]}'); [print(f'  → {l[\"timestamp\"][:19]} {l[\"actor\"]:8s} {l[\"action\"]:30s} {l.get(\"target_type\",\"\")}/{(l.get(\"target_id\") or \"\")[:8]}') for l in d['logs'][:10]]" 2>/dev/null
success "Compliance-grade audit trail: who, what, when, on what target"
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 11: V2G Arbitrage (Feature 10)
# ═══════════════════════════════════════════════════════════════════════════
if ! $SKIP_V2G; then
  header "BEAT 11: V2G Arbitrage with Real Spot Prices"
  narrate "Optimizing EV battery charge/discharge schedule to maximize revenue..."
  V2G_RESP=$(api_get "/agents/v2g-dispatch?horizon_hours=12" 2>/dev/null)
  echo "$V2G_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Devices: {d.get(\"devices_used\",0)}  Revenue: \${d.get(\"total_projected_revenue_dollars\",0):.2f}  Degradation: \${d.get(\"total_deg_cost_dollars\",0):.2f}  Net: \${d.get(\"total_projected_revenue_dollars\",0)-d.get(\"total_deg_cost_dollars\",0):.2f}')" 2>/dev/null
  V2G_SLOTS=$(echo "$V2G_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); active=[s for s in d.get('schedule',[]) if s['action'] in ('charge','discharge')]; [print(f'  → {s[\"action\"]:10s} {s[\"power_kw\"]}kW @ \${s[\"spot_price_per_kwh\"]:.4f}/kWh → \${s[\"net_revenue_dollars\"]:+.2f}') for s in active[:6]]" 2>/dev/null)
  echo "$V2G_SLOTS"
  success "V2G optimizer maximizes revenue net of battery degradation (Arrhenius model)"
  sleep $WAIT_BEAT
fi

# ═══════════════════════════════════════════════════════════════════════════
# BEAT 12: Aegis Auto-Remediation
# ═══════════════════════════════════════════════════════════════════════════
header "BEAT 12: Aegis Auto-Remediation Engine"
narrate "Triggering an on-demand Aegis scan — the fleet heals itself..."
AEGIS_RESP=$(api_get "/aegis/scan" 2>/dev/null)
echo "  Scan completed"
AEGIS_SUMMARY=$(api_get "/aegis/summary" 2>/dev/null)
echo "$AEGIS_SUMMARY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Total: {d.get(\"total\",0)}  Auto-resolved: {d.get(\"auto_resolved\",0)}  Escalated: {d.get(\"escalated\",0)}  Active: {d.get(\"active\",0)}')" 2>/dev/null
success "Aegis: scrape → classify → decide → act. 8 rules, DLQ, dry-run mode, full audit trail"
sleep $WAIT_BEAT

# ═══════════════════════════════════════════════════════════════════════════
# Closing
# ═══════════════════════════════════════════════════════════════════════════
header "DEMO COMPLETE"
echo -e "${BOLD}${GREEN}"
echo "  What you just saw:"
echo "  ✓ 16 database tables — devices, firmware, telemetry, geofences, shadows..."
echo "  ✓ 99 REST API routes — full CRUD for every feature"
echo "  ✓ 6 AI agents — OTA, anomaly, groups, onboarding, V2G, predictive"
echo "  ✓ 8 Aegis remediation rules — auto-healing with DLQ"
echo "  ✓ 30+ Prometheus metrics — full observability"
echo "  ✓ 91 unit tests + 40 E2E tests — production-ready"
echo "  ✓ Ed25519 firmware signing — cryptographic integrity"
echo "  ✓ Real spot-price integration — V2G with actual market data"
echo "  ✓ Device shadows — AWS IoT digital twin pattern"
echo "  ✓ Offline command queue — no commands lost"
echo "  ✓ Geofencing — enter/exit alerts on live map"
echo "  ✓ Predictive maintenance — failures predicted before they happen"
echo "  ✓ Scheduled OTA — blackout windows + canary rollouts"
echo "  ✓ Audit log — compliance-grade traceability"
echo "  ✓ RBAC roles — user/admin/operator/viewer/fleet_manager"
echo "  ✓ Bulk CSV import + QR-claim — provision at scale"
echo ""
echo "  Dashboard:  ${BASE_URL}/"
echo "  API Docs:   ${BASE_URL}/docs"
echo "  Grafana:    http://localhost:3000"
echo "  Prometheus: http://localhost:9090"
echo -e "${RESET}"
