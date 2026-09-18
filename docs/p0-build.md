# P0 Build — Design, Architecture, Hardware & Execution Log

Closes the **remainder** of G-01…G-05 from `docs/aiot-gap-analysis.md` §3 (what the
MVP slice left open). Every item lands behind existing topics/endpoints/schemas,
additive only. Status of the MVP foundation: `USE_CASES.md` UC-28/29/30 Done.

Assumptions (documented; correct on sight): hardware reality = real or emulated
OBD-II feed available; TimescaleDB accepted in the production profile; edge target
is x86 gateway first, ARM64 second, ESP32 stays a thin publisher.

---

## 1. Hardware reality + wiring

### 1.1 Topology

```
 [Vehicle]                    [Gateway bench]                    [Server]
 OBD-II DLC ◄── cable ──► ELM327 adapter ── USB/serial ──► x86 laptop / RPi
 (J1962 16-pin)   (CAN-H/L +      (UART 38400                │  edge/obd_gateway.py
                   pwr/gnd)        8N1)                       │  polls PIDs, snapshots DTCs
                                                              │  publishes iot/fleet/+/obd
                                                              ▼
                                                     WiFi/Ethernet ──► Mosquitto ──► backend
```

Simultaneous paths (all live, `source` tag distinguishes):
`sim` (simulator, default) · `obd` (this gateway) · `oem` (future API pull) · `edge` (P0-E verdicts).

### 1.2 OBD-II DLC (J1962 Type B) pinout — passenger vehicles, CAN 11-bit 500 kbps

```
  ┌─────────────────────────────────┐
  │ 1  2  3  4  5  6  7  8          │   4  = chassis ground ──► common gnd
  │ 9 10 11 12 13 14 15 16          │   5  = signal ground  ──► common gnd
  └─────────────────────────────────┘   6  = CAN-H ──► ELM327 CAN-H
                                        14 = CAN-L ──► ELM327 CAN-L
                                        16 = +12V (always on, 4A fused) ──► adapter pwr
```

Wire list (bench cable, twisted pair for 6/14, ≤1 m stub):

| From (DLC) | To (ELM327 side) | Notes |
|---|---|---|
| Pin 6 CAN-H | CAN-H in | twisted with 14, 120 Ω termination lives in vehicle + adapter |
| Pin 14 CAN-L | CAN-L in | — |
| Pin 4 + 5 | GND | star-point to gateway GND; no ground loops via USB isolator if bench PSU used |
| Pin 16 +12V | VIN (via 2A fuse) | adapter draws <100 mA; fuse protects the vehicle circuit |

Heavy-duty variant (J1939, 29-bit 250 kbps, Deutsch 9-pin): C=_CAN-H, D=CAN-L,
A=GND, B=+12/24V. Same gateway code path, different `protocol=` + baud; PIDs
replaced by SPN/FMI frames (decoder stub reserved in `app/obd/dtc.py` future section).

### 1.3 ELM327 → gateway link

```
 ELM327 UART @ 38400 8N1                Gateway (x86/ARM64)
 TX ─────────────────► RX (USB-serial /dev/ttyUSB0, or BT SPP /dev/rfcomm0)
 RX ◄───────────────── TX
 GND ───────────────── GND
```

Init string (gateway sends on open): `ATZ → ATE0 → ATL0 → ATS0 → ATH1 → ATSP6`
(reset, echo off, linefeeds off, spaces off, headers on, protocol 6 = ISO 15765-4
CAN 11/500). Poll loop: `010C 010D 0105 012F 0146 01A6` (RPM, speed, coolant,
fuel, ambient, odometer where supported) every `OBD_POLL_SECONDS`; `03` (stored
DTCs) on MIL or every Nth poll; `0902` VIN once at connect (binds device identity).

### 1.4 ESP32 thin node (unchanged role)

```
 ESP32 + CAN transceiver (e.g. TJA1050) ──► WiFi ──► iot/fleet/{id}/heartbeat
   (reads CAN, publishes raw-ish heartbeat; NO inference — RAM/model mismatch)
```

ESP32 never runs the IF model (documented constraint); if a use case ever demands
on-MCU inference, it needs a separate TinyML track (not P0).

### 1.5 Bench bring-up checklist

1. Cable DLC→ELM327 per §1.2, fuse on pin 16. 2. `screen /dev/ttyUSB0 38400`,
   `ATZ` → `ELM327 vX.Y`. 3. Ignition ON (engine off is fine for PIDs+DTC read).
   4. `0100` → `41 00 BE 1F B8 10` (PID support map). 5. Start gateway (§3, step 1),
   watch `iot/fleet/+/obd` on the broker. 6. Confirm backend `fleet_telemetry_points_total{device}` increments and `fleet_telemetry_duplicates_total{source="obd"}` stays flat.

---

## 2. P0-A — Real signal ingest (G-01 remainder, M, ~2 wks)

1. **`edge/obd_gateway.py`** (FUTURE — not built): serial ELM327 driver per §1.3; emits
   `iot/fleet/{id}/obd` `{event_time, pid_map, dtcs[], odometer_km, fuel_level_pct, vin}`
   and `iot/fleet/{id}/bms` (EV cell data where exposed). Env: `OBD_PORT, OBD_POLL_SECONDS,
   DEVICE_ID, MQTT_*`. Offline-tolerant: local spill file, replay on reconnect.
2. **Backend subscribe** (`app/mqtt_client.py` +/obd, +/bms, QoS1) → `_record_telemetry`
   with `source="obd"`, **event-time preserved** (`payload.event_time`, fallback `utcnow()`).
3. **Idempotency:** runtime LRU of `(device_id, event_time, source)`; dupes counted
   (`fleet_telemetry_duplicates_total{source}`), never inserted.
4. **Odometer guard:** per-device last-odo memory; rollbacks rejected + counted
   (`fleet_telemetry_rejected_total{reason="odo_rollback"}`).
5. **`app/obd/dtc.py`:** decode table (P0xxx/C0xxx/B0xxx/U0xxx families + known
   P0128/C0745/U0100 meanings) → enriches prediction evidence + agent messages.
6. **Broker:** `persistence true` both confs; ACL += `iot/fleet/%u/obd|bms|edge` write lines.
7. **e2e `test_43`:** event-time preserved, dupe suppressed, odo-rollback rejected.
8. **Exit:** 24h real/emulated feed, ingest loss <1% (gateway-sent vs DB counter).

## 3. P0-B — Time-series store (G-02, S–M, ~1–2 wks)

1. Production image → `timescale/timescaledb:latest-pg16` (FUTURE — still `postgres:16-alpine`; SQLite/demo untouched).
2. Leader-only boot: `create_hypertable('telemetry','timestamp', 1-day chunks)` when
   Timescale present else skip+log; composite `(device_id, timestamp DESC)` + `source` index.
3. Continuous aggregate `telemetry_5m` (avg/min/max signal/temp/soc, count; 30-min refresh);
   reads with `hours>24` use rollups, below use raw.
4. Retention worker (leader loop, OTA-scheduler pattern): enforce
   `telemetry_retention_days`, raw-drop >90d; metrics `fleet_retention_runs_total`,
   `fleet_telemetry_dropped_total`.
5. Router upgrades: `start/end` window, `source` filter, cursor pagination, multi-device
   `POST /telemetry/query`, tenant-scoped reads, OBD fields in `/stats`.
6. Exit: 10× synthetic load — p95 read <500ms, zero-loss ingest, rollup lag <1h.

## 4. P0-C — Real-data training + promotion (G-03 remainder, M–L, ~2–3 wks)

1. `scripts/label_outcomes.py` (FUTURE): predictions ⨝ work orders → labeled windows
   (WO close notes are the labels — incl. false-positive marks).
2. `scripts/train.py --data --out version` (FUTURE — today `scripts/train_mvp.py` on seeded scenarios): same features → registry `staging` with
   `{precision, recall, train_n}` metrics.
3. `app/routers/models.py` (admin/fleet_manager): `GET /models`, `POST /models/{v}/promote`
   (runs backtest gates, refuses on fail), demote; `?model=vN` incl. shadow scoring.
4. `backtest.py --version/--data`: load registry artifacts, wall-clock lead units.
5. `MODEL_STORAGE_PATH` through compose + dedicated `model_data` volume (FUTURE — env-only today).
6. Exit: ≥2 wks labeled-data model promoted through gates, canary subset, per-version FP/lead.

## 5. P0-D — Live eval (G-04 remainder, S, ~1 wk)

1. `scripts/inject_fault.py` (FUTURE — today `SIMULATOR_SCENARIO` env): arm simulator scenario on live stack, write onset manifest.
2. Nightly scorer: manifest ⨝ predictions → precision/recall/lead-hours → `eval/runs/`.
3. p95 inference-latency gate in backtest output (cloud <500ms; edge <100ms with P0-E).
4. Drift watch (lite): feature-mean vs bundle stats → `model_drift` alert (retrain = P1).
5. Exit: 5 consecutive green nights; drift alert proven on synthetic shift.

## 6. P0-E — Edge runtime (G-05, M–L, ~3–4 wks)

1. `edge/` service (`Dockerfile.edge`, demo+production): ONNX Runtime, pack
   `{model.onnx, thresholds.json, feature_spec.json}` via `scripts/export_onnx.py` (FUTURE).
2. Verdicts on `iot/fleet/+/edge` (never raw samples) → AlertEngine first-class
   anomalies + shadow reported-state; offline disk outbox with ordered replay.
3. KPIs: `fleet_edge_*` metrics, **edge-autonomy %** panel; delivery via OTA-scheduler
   canary machinery with FP/latency auto-rollback.
4. Staging: x86 container → ARM64 → (ESP32 stays thin, §1.4).
5. Exit: 24h + 3 link drops, zero verdict loss, cloud/edge agreement >90%.

## 7. Sequencing

```
P0-A (ingest) ──┐
P0-B (store) ───┼─▶ P0-C (train/promote) ─▶ P0-D (live eval, continuous)
P0-E (edge) ────┘   (needs A data + B retention + C export format)
```

~5–7 wks single engineer. Hard rule: P0 touches ingest/store/train/eval/edge only.

## 8. Build log

| Date | Item | Result |
|---|---|---|
| 2026-09-17 | Doc created (design + wiring + checklist) | this file |
| 2026-09-17 | P0-A1: OBD/BMS topics, event-time, dedup LRU, odo guard, `app/obd/dtc.py`, `/obd/dtc` API, ACL + persistence | live-verified: 1 row w/ producer time, dupe→`duplicates_total`, rollback→`rejected_total` |
| 2026-09-17 | P0-A2: bounded batch ingest worker + queue metrics | live-verified: `batches_total` increments, depth 0 |
| 2026-09-17 | e2e 46/46 (incl. test_43), eval 5/5 unaffected | testing profile, clean volumes |
| 2026-09-18 | OBD vehicle simulator: profiles/VIN/cells, `+/obd\|+/bms` topics, Device VIN fields, cell summary, twin `vehicle` block (UC-31) | live-verified topics + twin; e2e 47/47; twin-tab screenshot |
| 2026-09-18 | P-ret-1: Alembic + reshape migration (proven on legacy SQLite), tenant/region stamping, 24h-hot retention worker + 5m rollups (UC-32) | e2e 48/48, eval 5/5; 7-day default enforced |
