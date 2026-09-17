# Fleet Commander — AIoT Gap Analysis, Implementation Plan & Target Design

**Status:** Design document (build order after approval of phasing in §9).
**Build status (2026-09-16):** MVP slice DONE — DATA-01 (UC-30), ML-01+EVAL-01 (UC-29),
WO-01 (UC-28), TWIN-01 (UC-15). See `USE_CASES.md` status map for the verified record.
**Scope:** Compare Fleet Commander against commercial fleet platforms (Samsara, Geotab, Intangles-style digital twin) and edge-AIoT practice; register every gap; specify exactly what to change in this repo, file by file, and how each change moves an AIoT metric.
**Honesty boundary (read first):** this repo today contains **no trained ML models and no on-device inference**. “Predictive” = hand-coded linear slopes (`app/predictive_maintenance.py`), V2G = greedy heuristic + mock prices (`app/v2g_optimizer.py`, `app/spot_prices.py`), Aegis = threshold rules (`app/aegis/engine.py`), agents = deterministic recommenders. Nothing in this doc may be claimed as “AI” until the eval harness in §7 exists and passes. Default benchmark focus: all three commercial lenses briefly (Samsara = safety/video-led, Geotab = open data-led, Intangles = twin/predictive-led).

---

## 1. Where the system stands (verified, read-only survey)

- **Runtime:** FastAPI + async SQLAlchemy (SQLite dev / Postgres prod), Mosquitto MQTT, Prometheus + Grafana, Docker Compose. Entry: `app/main.py:502-642` (lifespan, leader/api roles, schedulers). 19+ routers + `agents/routers.py` + `app/aegis/router.py`.
- **Solid primitives (keep):** OTA state machine with rollback (`app/ota_manager.py:16-40`, timeout watcher, 3 retries); alerts with dedup/cooldown/escalation (`app/alert_engine.py:142-258`); geofences (`app/geofence_checker.py`); device shadow desired/reported (`app/routers/shadow.py`); lifecycle + claim tokens (`app/routers/lifecycle.py`); command queue with TTL (`app/routers/command_queue.py`); scheduled OTA with canary/blackout (`app/routers/scheduled_ota.py`); firmware SHA256 + optional Ed25519 (`app/firmware_signing.py:58-113`); API keys, device certs/PKI, audit, webhooks, multi-org.
- **UI:** Jinja dashboard (`app/templates/dashboard.html`, 1681 lines, 5–60s polling), Grafana 13 panels (`docker/grafana/dashboards/fleet_dashboard.json`), static docs site (`website/`). Simulator: 15 virtual devices, 10s heartbeat, 20% random OTA fail (`simulator/simulator.py`).
- **What “AI” really is today:** slope heuristics (≥5 points, ≤24h, 4 checks, max-risk-only), greedy V2G, 3-metric Aegis classifier + 8 fixed actions, recommend-only agents (`human_input_required:true`). No sklearn/torch/TF, no model files, no edge inference.

## 2. Commercial bar (2026)

- **Samsara (safety/video-led):** native AI dashcams, edge detections (blind spot, rear collision, bird’s-eye), Coaching Priority over 45+ risk factors, AI ride-alongs (22-factor standard), Connected Maintenance (fault insights, shop planner, warranty agents), Agent Studio (15+ templates, permissions/usage).
- **Geotab (open data-led):** GPS-first GO devices, 37T datapoints/6M vehicles, Geotab Intelligence predictive layer, Ace conversational analytics, MCP connector (Claude/ChatGPT/Copilot), fuel-transaction mismatch detection, ML tire-pressure baselines, automated work requests, 200+ marketplace apps.
- **Intangles (twin/predictive-led):** physics + ML twin per asset, 450+ signals, 85–95% fault accuracy, 20–45 day lead (60–90 for batteries), component health scoring → automated work orders + audit trail.
- **Edge-AIoT practice:** sub-100ms on-vehicle inference (TensorRT/ONNX), compact events not raw frames, offline-capable tiering, model canary + rollback, drift/retrain loop, OBD-II/CAN + cameras + BMS as core inputs. Video telematics at ~46% fleet adoption; >90% of new commercial vehicles ship embedded telematics.

## 3. Gap register

Severity: P0 = blocks any credible AIoT claim. Effort: S <1wk, M 1–3wks, L 1–2mo (single engineer).

| # | Gap | Today (file ref) | Commercial bar | Sev | Effort |
|---|---|---|---|---|---|
| G-01 | No real vehicle signals (OBD-II/CAN/J1939, DTCs, TPMS, BMS cell data) | Synthetic heartbeat fields only (`simulator/simulator.py:323-351`, `app/models.py:260-278`) | OBD-II gateways, OEM APIs, curve logging | P0 | M |
| G-02 | No time-series store / retention tiers / feature views | 30-day `Telemetry` table, no hypertable (`app/models.py:260-278`) | Hypertables, downsampling, feature store | P0 | S–M |
| G-03 | No ML training/eval/registry/inference service | No ML deps (`requirements.txt:1-20`) | Versioned models, backtests, shadow deploy | P0 | M–L |
| G-04 | No eval harness (precision/recall/lead-time, fault injection) | E2E tests cover CRUD only (`tests/test_e2e.py`, 638 lines) | FP<1%, latency gates, canary cohorts | P0 | S–M |
| G-05 | All inference cloud-side; no edge runtime, no offline tier | Simulator is a cloud MQTT client | Sub-100ms edge, store-and-forward | P0 | M–L |
| G-06 | Shadow is equality check, not a twin (no versioning policy, no simulation) | `GET /shadow/{id}` compares payloads (`app/routers/shadow.py`) | Versioned replica + what-if simulation | P1 | M |
| G-07 | Predictive = slopes, single-winner, ≤24h, uncalibrated confidence | `_linear_slope`, `confidence=n/20` (`app/predictive_maintenance.py:31-176`) | Per-component RUL, 20–90d lead, calibrated risk | P1 | M |
| G-08 | V2G on mock prices, greedy, no SoH forecast, dispatch not persisted | `mock_spot_prices()`, `heuristic_optimize()` (`app/v2g_optimizer.py`, `app/spot_prices.py`) | Real tariffs, MILP/solver, degradation-aware dispatch | P1 | M |
| G-09 | Alerts stop at notify; no work-order model / shop flow / parts | `AlertEngine` → Slack/SMTP/webhook (`app/alert_engine.py:241-258`) | Rules→tickets, shop planner, SLA, warranty | P1 | M |
| G-10 | No driver identity / behavior scoring / coaching loop | Threshold anomalies only (`agents/async_tools.py:192-293`) | 0–100 scores, video coaching, ride-alongs | P1 | M–L |
| G-11 | No video/event pipeline (capture, edge detect, metadata, blur, clips) | Absent | AI dashcam, DMS/ADAS, exoneration flow | P2 | L |
| G-12 | No model OTA (version, canary, rollback on FP/latency breach) | Firmware OTA solid; no model artifact path | Model registry → staged rollout | P1 | S–M |
| G-13 | No fuel/energy metering or fraud (card vs tank vs location) | Absent | Mismatch detection, ESG reports | P2 | M |
| G-14 | No routing/dispatch/compliance (ELD/HOS/DVIR/IFTA/tacho) | Absent | Commercial routing, compliance workflows | P2 | L |
| G-15 | No BI/MCP fan-out ( Historic export, assistant connectors) | REST + webhooks only | Data Connector, MCP (Claude/ChatGPT/Copilot) | P2 | S–M |
| G-16 | Single-leader loops, no partitioning/backpressure policy | `app/main.py:540-567` loops | Sharded ingest, queue depth shedding | P2 | M |

## 4. AIoT use-case catalog (each: bar → today → gap → adaptation pointer)

Status refs point to `USE_CASES.md` “Current status map” (verified 2026-09-16).

- **UC-A1 Edge anomaly detection (offline-resilient):** bar <200ms local verdicts, buffered sync. Today: none — **no existing UC (new)**. Gaps G-05/G-01. → §5.5 design EDGE-01.
- **UC-A2 Predictive maintenance with lead time:** bar 20–90d lead, 85–95% acc, ranked risk. Today: UC-16 **Done\*** (slope heuristics, no lead metric). Gaps G-03/G-04/G-07/G-02. → ML-01/EVAL-01.
- **UC-A3 Battery SoH/RUL + smart charging:** bar cell-level degradation, preconditioning, tariff arbitrage. Today: UC-09 **Done\*** (greedy + mock) + UC-18 **Partial**. Gaps G-08/G-01. → V2G-01.
- **UC-A4 Driver scoring → coaching loop:** bar 0–100 scores, event review, behavior change. Today: none — **no existing UC (new)**; UC-07 anomalies are asset-side only. Gaps G-10/G-01. → DRV-01.
- **UC-A5 Video events → metadata + review:** bar edge DMS/ADAS, blur, exoneration. Today: none — **no existing UC (new)**. Gap G-11. → VID-01 (P2, stub only).
- **UC-A6 Per-asset digital twin:** bar versioned replica + simulation. Today: UC-15 **Done\*** (desired/reported check). Gap G-06. → TWIN-01.
- **UC-A7 Model OTA with canary:** bar staged model rollout + auto-rollback. Today: UC-04/05/06 **Done** for firmware; no model artifact path. Gap G-12. → MOTA-01.
- **UC-A8 Fuel/energy anomaly + fraud:** bar card-vs-consumption mismatch. Today: none — **no existing UC (new)**. Gap G-13. → FUEL-01 (P2).
- **UC-A9 Alert → work order automation:** bar tickets, shop planner, SLA. Today: UC-07 **Done** stops at notify; no WO model. Gap G-09. → WO-01.
- **UC-A10 Fleet learning loop:** bar retrain → staged deploy, per-release gates. Today: none — **no existing UC (new)**. Gaps G-03/G-04. → LOOP-01.

## 5. Implementation design (change-by-change)

Conventions: new tables get `org_id` (tenancy pattern `app/database.py:40-82`); new endpoints follow role ranks (`app/deps.py:31-47`); every new signal gets a Prometheus metric (`app/metrics.py`) + Grafana panel + e2e test. Existing polling dashboard stays; new cards reuse `stat-card`/`badge` CSS.

### 5.1 DATA-01 — Real signal ingest + time-series store (closes G-01/G-02, enables UC-A1/A2/A3/A4)

**Changes:**
1. `app/models.py`: extend `Telemetry` with `dtc_codes JSON, odometer_km, fuel_level_pct, tire_pressures JSON, cell_voltages JSON, source ENUM(sim|obd|oem|edge)`. New `Te서를` — no; keep single table. Alembic-style migration via `init_db` + backfill (pattern `app/database.py:54-82`).
2. `docker-compose.yml` + prod profile: add TimescaleDB hypertable on `telemetry(timestamp)` when Postgres (documented fallback: SQLite keeps current table). Retention job: downsample >30d to 5-min rollups (`telemetry_5m` table), drop raw >90d (config `telemetry_retention_days`, `telemetry_rollup_days` in `app/config.py`).
3. `app/mqtt_client.py`: subscribe `iot/fleet/+/obd` (DTC/fuel/odo/TPMS) and `iot/fleet/+/bms` (cell voltages, pack temp); route to `_record_telemetry` (`app/main.py:76-99`) with `source` tag.
4. `simulator/simulator.py`: `--obd` mode emitting DTC bursts, fuel drain, TPMS sag on a schedule so demos/evals have ground truth (fault-injection flags double as EVAL-01 fixtures).
5. `app/metrics.py`: `fleet_telemetry_points_total{device,source}`, `fleet_telemetry_lag_seconds`. Grafana: ingest-lag + points/sec panels.
6. `tests/test_e2e.py`: `test_obd_ingest`, `test_rollup_job` (rollup on SQLite-tolerable path or pg-only mark).
**How it helps:** every downstream AIoT claim needs labeled, timestamped, per-source signals with known retention. This turns telemetry from “demo fields” into a feature-grade store; without it ML-01 has no training data and EVAL-01 has no ground truth.

### 5.2 ML-01 — Inference service + model registry (closes G-03, enables UC-A2/A3)

**Changes:**
1. New package `app/ml/`: `features.py` (windowed feature builder over telemetry: slopes, variances, duty-cycle stats, DTC counts), `registry.py` (`MLModel` table: name, version, artifact_path, metrics JSON, stage=staging|production|archived, trained_at), `inference.py` (`predict(device_id)` → list of `{risk_type, risk_score, confidence, hours_to_failure, evidence}`).
2. `requirements.txt`: add `scikit-learn` (+ `joblib`) only — IsolationForest + GradientBoosting for v1; no torch/TF until video (G-11).
3. `app/routers/predictive.py`: keep `POST /predictive/scan` contract, but route through `inference.py` with fallback to legacy slopes when no production model (`?model=auto|legacy|vN` for debugging). Response schema unchanged (`app/schemas.py:361-378`) + `model_version` field.
4. `app/metrics.py`: `fleet_ml_inference_latency_seconds`, `fleet_ml_predictions_total{risk_type,model_version}`, `fleet_ml_fallback_total`.
5. Dashboard: predictive cards show `model vN` chip + calibrated confidence; no layout change.
**How it helps:** creates the versioned seam between heuristics and learning — legacy stays as fallback, models become measurable artifacts instead of claims. Every prediction becomes attributable to a model version, which EVAL-01 and LOOP-01 require.

### 5.3 EVAL-01 — Eval harness + fault injection (closes G-04, gates UC-A2/A1/A7/A10)

**Changes:**
1. New `tests/test_ml_eval.py` (marked slow/pg-optional): seeded fault-injection scenarios (bearing drift, thermal runaway, TPMS sag, SOC cliff) generated by simulator `--obd --scenario` flags; asserts per-scenario **precision/recall, false-positive rate, median lead-time hours** vs thresholds in `tests/eval_thresholds.yml` (e.g. FP<5% v1, lead>24h on drift scenarios).
2. `scripts/backtest.py`: replays retained telemetry through any registry version, emits comparison table (legacy vs vN) — used in PRs touching `app/ml/` or `app/predictive_maintenance.py`.
3. CI gate (document in `SCALING.md` or new `ml/ABLATION.md` pointer): model promotion staging→production requires backtest pass + EVAL pass.
**How it helps:** converts “predictive” from adjective to number. Blocks promotion of models (and docs claims) that don’t beat the legacy baseline on lead time at fixed FP — the exact bar commercial buyers apply.

### 5.4 TWIN-01 — Shadow → versioned digital twin (closes G-06, enables UC-A6)

**Changes:**
1. `app/models.py`: `DeviceShadow` gains `supersedes_version`, `source(cloud|edge|device)`, `ttl`; `PUT /shadow/{id}` implements last-writer-wins with conflict response (409 + both versions) instead of silent overwrite; `GET /shadow/{id}/history` already exists — add `?since_version`.
2. New `GET /twin/{id}`: merged view `{state, health_score, open_risks[], active_schedule_refs[], last_sync}` joining shadow + latest predictions + lifecycle + V2G schedule — the single “asset page” API the dashboard detail modal and future BI use.
3. `app/mqtt_client.py`: `.../command/shadow` gains `base_version` so edge/device can reject stale desired-state pushes.
4. Dashboard device-detail modal: new “Twin” tab (5th tab) rendering merged view; existing tabs untouched.
**How it helps:** twin becomes a conflict-aware, versioned record instead of a last-write cache — prerequisite for simulation/what-if (UC-A6) and for edge+cloud both writing state without clobbering (UC-A1).

### 5.5 EDGE-01 — Edge runtime + store-and-forward (closes G-05, enables UC-A1/A5-stub)

**Changes:**
1. New `edge/` service (own Dockerfile, `demo` + `production` profiles in `docker-compose.yml`): subscribes to local sensor/OBD feed, runs ONNX/quantized model (v1: same IsolationForest exported to ONNX + threshold pack), publishes compact verdicts to `iot/fleet/{id}/edge` (never raw frames/samples); buffers to disk when broker unreachable, replays on reconnect with `replayed:true`.
2. `app/mqtt_client.py`: subscribe `iot/fleet/+/edge`; verdicts enter `AlertEngine` as first-class anomalies (dedup key `edge:{type}:{device}`) and `DeviceShadow` reported-state.
3. `app/metrics.py`: `fleet_edge_verdicts_total{type}`, `fleet_edge_offline_seconds_total`, `fleet_edge_replay_total`. Grafana: edge-autonomy % panel.
4. OTA reuse: edge model pack distributed via scheduled-OTA machinery (see MOTA-01).
**How it helps:** moves sub-second decisions to where the physics happens and makes the cloud resilient to link loss — the defining AIoT split (edge reacts, cloud learns). Autonomy % becomes a reportable KPI.

### 5.6 MOTA-01 — Model OTA via existing scheduler (closes G-12, enables UC-A7/A10)

**Changes:**
1. `app/models.py`: `Firmware.kind ENUM(firmware|edge_model|cloud_model)` + `model_version`, `min_edge_runtime`; `OtaSchedule` gains `success_gate JSON {max_fp_rate, max_latency_ms}` and `rollback_on_breach bool`.
2. `app/routers/scheduled_ota.py` + `app/ota_manager.py`: after canary cohort completes, evaluate gate from EVAL-01 metrics endpoint; breach → auto-create rollback schedule to previous version (reuse `publish_rollback_command` pattern).
3. Dashboard schedules panel: gate status chip per campaign (pass/breach/pending).
**How it helps:** models get the same safe-delivery machinery firmware already has (canary/blackout/rollback) — the missing half of “OTA” in an AIoT fleet, and the enforcement arm of LOOP-01.

### 5.7 V2G-01 — Real prices + solver + SoH forecast (closes G-08, enables UC-A3)

**Changes:**
1. `app/spot_prices.py`: production provider path (config `spot_price_url/key` already exists) + tariff-table fallback CSV (`spot_price_provider=tariff`, file path config) so demos don’t depend on mock randomness; persist fetched price curves (`SpotPrice` table, 1 row/slot/region).
2. New `app/v2g_solver.py`: MILP via `pulp` (new dep, CBC bundled) maximizing revenue − degradation over horizon with SOC 20–90%, SOH<70% block (keep), departure constraint; heuristic stays as fallback (`?solver=milp|heuristic`).
3. Persist dispatches: `POST /agents/v2g-dispatch` writes `V2gSchedule` rows (today read-only model) + publishes `command/v2g`; add `GET /v2g/schedules` history.
4. SoH forecast: per-device linear/exponential fit over retained telemetry → `predicted_soh_30d`; surfaced on twin view (TWIN-01) and dispatch response.
**How it helps:** V2G becomes auditable (persisted prices + schedules + solver version) and actually optimizes instead of greedily reacting — the difference between a demo and a money-affecting controller.

### 5.8 WO-01 — Alert → work order (closes G-09, enables UC-A9)

**Changes:**
1. `app/models.py`: `WorkOrder(id, alert_id FK, asset, title, severity, status=open|in_progress|done|cancelled, assignee, due_at, parts JSON, cost_estimate, closed_at, org_id)`; `Alert` gains `work_order_id`.
2. `app/alert_engine.py`: rule→template map (`workorder_templates.yml`: e.g. `stuck_ota` → “retry + rollback verify” checklist); auto-create on 3rd escalation (existing threshold `app/alert_engine.py:202-211`) or manual “Create work order” button on dashboard alert cards.
3. New `app/routers/workorders.py`: CRUD + `POST /workorders/{id}/close{resolution,parts_used,cost}`; metrics `fleet_workorders_total{status}`, `fleet_mttr_hours`.
4. Dashboard: alerts panel gains WO badge/link; new “Maintenance” section reusing schedule-item CSS.
**How it helps:** closes the loop from detection to action with SLA/MTTR accounting — the operational ROI story (downtime $, not alert counts) that commercial platforms sell.

### 5.9 DRV-01 — Driver scoring + coaching (closes G-10, enables UC-A4)

**Changes:**
1. `app/models.py`: `Driver(id, name, license_ref, org_id)`, `Trip(id, device_id, driver_id, started/ended, harsh_brake/accel/corner counts, idle_min, overspeed_min, score)`; heartbeat-simulator extended with trip segments + harsh-event counters (source=sim/obd).
2. New `app/driver_scoring.py`: 0–100 score (weighted sub-scores, exposure-normalized per 100km); weekly rollup job (reuse scheduler-loop pattern `app/main.py:564`).
3. `GET /drivers/{id}/score?window=7d`, dashboard “Safety” section (reuse predictive-panel CSS + risk-meter), coaching note template on low-score drivers.
**How it helps:** adds the human half of fleet risk (behavior → wear → incidents) that pure asset telemetry misses; score trend is the coaching KPI insurers and safety teams actually buy.

### 5.10 LOOP-01 + FUEL-01 + VID-01 + BI-01 (P2, stub-first)

- **LOOP-01 (UC-A10):** `scripts/label_outcomes.py` (close predictions with work-order resolutions → labeled set), `scripts/train.py` (features → model → registry staging), promotion = backtest + EVAL-01 pass → MOTA-01 canary. Doc-only in P0; scripts land in P1.
- **FUEL-01 (UC-A8, G-13):** `FuelTransaction` table + CSV/import endpoint + mismatch job (card vs telemetry consumption vs geofence location); anomaly type `fuel_mismatch` reuses AlertEngine. Stub: schema + import endpoint first.
- **VID-01 (UC-A5, G-11):** architecture reservation only — event schema `{clip_ref, detections[], blur_applied}`, edge publishes metadata, cloud stores refs not frames. No video bytes in P0/P1.
- **BI-01 (G-15):** read-only `GET /export/telemetry.csv|parquet?since` + documented MCP mapping (twin view → assistant context). Cheap, high demo value.

## 6. Target architecture

```
Sensors/OBD-II/CAN/BMS/cameras ──▶ EDGE gateway (ONNX, <100ms verdicts, disk buffer)
        │ compact verdicts: iot/fleet/+/edge          │ replay on reconnect
        ▼                                             ▼
Mosquitto (1883/8883 mTLS) ◀── simulator --obd (fault scenarios, ground truth)
        │
        ▼
FastAPI leader: ingest (obd/bms/edge topics) → Timescale telemetry + rollups
   ├─ ML inference (registry versions, fallback legacy) → predictions (w/ model_version)
   ├─ AlertEngine (dedup/cooldown/escalation) → notify + auto work orders (WO-01)
   ├─ Aegis rules (existing) + twin merge (TWIN-01) + driver scoring (DRV-01)
   ├─ V2G solver MILP + persisted prices/schedules (V2G-01)
   └─ /metrics → Prometheus → Grafana (new: ml/edge/twin/WO panels)
EVAL-01 gates every model/scheduler change; MOTA-01 ships models via canary OTA.
```

## 7. Acceptance criteria & KPIs (no claim without these)

- EVAL-01 thresholds live in `tests/eval_thresholds.yml`; v1 bars: FP<5% on drift scenarios, median lead>24h, inference p95<500ms cloud / <100ms edge (on declared hardware), backtest beats legacy on lead@fixed-FP.
- Fleet KPIs: unscheduled downtime hrs, MTTD, % edge decisions without cloud, OTA success rate (existing), MTTR (new WO-01), V2G net $/vehicle/mo (new), driver score trend (new).
- Docs rule: any “AI/predictive” sentence must cite model version + eval table, or stay labeled heuristic.

## 8. What stays untouched

OTA firmware path, PKI/certs, RBAC roles, alert lifecycle semantics, dashboard polling architecture, Grafana datasource/provisioning, compose profiles (extend, don’t rename), e2e contract style (live-HTTP, `BASE_URL`).

## 9. Phased build order (proposed)

- **P0 (credibility foundation, ~3–5 wks):** DATA-01 (ingest+store) → EVAL-01 (harness+thresholds) → ML-01 (registry+inference+fallback) → backtest-vs-legacy report. Exit: one model version beating slopes on lead@fixed-FP, all behind existing API contracts.
- **P1 (closed loops, ~4–6 wks):** TWIN-01 → MOTA-01 → EDGE-01 (simulated edge first, hardware second) → WO-01 → V2G-01 → DRV-01 → LOOP-01 scripts.
- **P2 (parity plays):** FUEL-01, VID-01 metadata path, BI-01 export/MCP, routing/compliance scoping, scale-out (G-16).

Say the word on phasing (or reprioritize DRV-01/WO-01 earlier for demo value — they’re the most visible) and P0 splits cleanly into reviewable PRs per §5 item.
