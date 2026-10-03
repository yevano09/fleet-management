# Fleet Commander — Demo Talking Script

Read this alongside the HTML deck (`presentation.html`) and the live dashboard
at http://localhost:8181. Roughly 8 minutes end to end.

---

## Slide 1 — Title (0:00)

> "This is Fleet Commander. An IoT fleet management platform built for real
> vehicles — FastAPI, SQLAlchemy, Mosquitto MQTT, Prometheus, Grafana, Docker
> Compose. 35 use cases, 144 tests, production-hardened with mTLS, RBAC, and
> PostgreSQL HA."

## Slide 2 — The Problem (0:20)

> "Managing a fleet of connected vehicles is hard. Firmware updates fail and
> brick devices. Devices go offline. You can't predict breakdowns. You lose
> cargo to temperature breaches. And 'AI' in most fleet tools is a buzzword —
> a linear regression slope with no eval gate guarding it. Fleet Commander
> closes all of these gaps."

## Slide 3 — Architecture (1:00)

> "At the centre is a FastAPI backend. Devices — simulators, ESP32 hubs, or
> the Arduino UNO Q — register over MQTT, stream heartbeats, OBD-II, BMS cell
> data, and cargo telemetry. The backend writes to SQLite in dev or PostgreSQL
> in production, holds a leader/api replica split, runs a telemetry batch
> worker, a retention worker, and the Aegis auto-remediation engine. Every
> metric flows to Prometheus and Grafana. The dashboard UI polls every 5 to
> 60 seconds and renders a Leaflet map, a vehicle tree, a twin view, and a
> copilot chat bubble."

## Slide 4 — Live Demo: OTA + Rollback (2:00)

> "Let me show you the most important flow — an OTA firmware update."
>
> *Click "Trigger OTA Update", select firmware 2.0.0, confirm.*
>
> "The dashboard refreshes every 5 seconds. Watch the status column. Most
> devices go downloading → applying → verifying → success. But 20% of the
> fleet hits a hash mismatch — and look, the system automatically cascades
> into rollback, restores the previous firmware, and stamps the deployment
> rolled_back. No operator intervention. That's a bricked truck prevented."

## Slide 5 — Predictive Maintenance with Real ML (3:30)

> "Next, prediction. This isn't a slope heuristic. POST /predictive/scan
> runs a seeded IsolationForest — 200 trees, trained on normal telemetry —
> with attribution across signal, thermal, TPMS, fuel, and DTC groups."
>
> *Show a seeded fault scenario:*
>
> "I've armed a TPMS sag on one device. The scan flags tire_pressure with
> risk 0.6 and the model version mvp-iforest-v1. The legacy slope scorer is
> blind to tire pressure — the eval harness proves it: TPMS legacy lead time
> is 0/10. The model beats it."

## Slide 6 — The AIoT Loop: Predict → Alert → Work Order → Twin (4:15)

> "Prediction is useless without action. The driver scoring alert fires,
> dedups, cools down, escalates. At the third escalation, the alert engine
> auto-opens a maintenance work order from a template. The operator closes it
> with a resolution, parts, and cost — and the MTTR histogram updates. The
> digital twin view at GET /twin/{id} merges health score, risks, work orders,
> cargo status, and vehicle identity into one asset page."

## Slide 7 — Smart Cargo & Copilot (5:00)

> "For cold-chain, each vehicle carries a CargoProfile. The +/cargo topic
> delivers bay temperature, humidity, door state, and IMU peak-g. A TTS
> estimator projects minutes-to-breach. Shock peaks get classified into
> HARD_DROP or CARGO_COLLISION. And the copilot — this is CrewAI with a
> privacy layer — answers operator questions using seven read-only tools,
> pseudonymising device names and degrading GPS before anything touches an
> LLM. Try it from the chat bubble."

## Slide 8 — Edge AI & UNO Q (6:00)

> "The ESP32 in earlier fleets could only run a threshold pack. The Arduino
> UNO Q fixes that. Its STM32 MCU samples DHT22 and MPU6050 at 2 Hz and
> serials JSON to its QRB Linux SoC, which runs the exact same
> IsolationForest ONNX model as the cloud. Verdicts — never raw samples —
> publish to iot/fleet/{id}/edge."
>
> *If a board is present, plug it in. Otherwise:*
>
> "No physical board yet, so we simulate: the gateway falls back to a thermal
> drift when /dev/ttyHS1 is absent, and Wokwi runs the exact STM32 sketch.
> When the board arrives from transit, it's a one-line change in the Makefile
> — WOKWI out, ttyHS1 in."

## Slide 9 — Production Hardening (7:00)

> "No demo is complete without security. AUTH_MODE=strict refuses default
> secrets at startup. Every REST route is RBAC-gated — viewer can't trigger
> an OTA, fleet_manager can't rotate certs. Automation uses SHA-256-hashed
> API keys, shown once. The MQTT broker enforces mTLS on 8883 with per-device
> ACLs — device A cannot publish to device B's topic. Certificates rotate and
> revoke with CRL reload. The fleet partitions by org_id. Docker Compose
> ships a postgres + leader/api-replica topology with mTLS Mosquitto.
> scripts/verify-p0.sh runs 39 checks and exits 0."

## Slide 10 — Roadmap (7:45)

> "Where next? Canary auto-promote when FP rate breaches gate, self-healing
> fleet loops, model OTA with eval-gated rollback, driver coaching scores,
> and a fleet capacity planner. The use-case map in USE_CASES.md tracks all
> 35 UCs from Done to Done-star to Partial — honest about which heuristics
> are stand-ins until LOOP-01 trains on real fleet data."

## Closing (8:00)

> "Fleet Commander. 35 use cases. 144 tests. Production hardened. The edge
> AI track runs the same model from cloud to UNO Q. You can clone it, docker
> compose it, and see the whole loop in under 10 minutes. Thank you."
