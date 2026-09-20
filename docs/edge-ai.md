# Edge AI — hardware roles, wiring & contracts (P0-E)

Three tiers. Each tier does ONLY what its silicon allows; the verdict
contract is identical everywhere so the cloud can't tell them apart.

## 1. Roles

| Tier | Hardware | Runs | Publishes | Never |
|---|---|---|---|---|
| Sensor node | Arduino Uno/Nano (2KB RAM) | DHT22 + MPU6050 sampling @2Hz | Serial JSON lines to ESP32 | Network, ML, MQTT |
| Edge sensor hub | ESP32-WROOM-32 | Threshold verdict engine (slopes + limits), cargo frames, OTA | `+/edge` verdicts (on flag only), `+/cargo`, heartbeat | Full IF model (RAM) |
| Edge gateway | Raspberry Pi 4 / x86 (`edge` compose profile) | Exported IsolationForest (ONNX) + z-gate over trailing-24 windows | `+/edge` verdicts, replayed spillover | Raw samples upstream |

## 2. Wiring

```
[DHT22+MPU6050] --I2C--> [Arduino] --Serial 115200--> [ESP32] --WiFi/MQTT--> broker
 gripping: Arduino TX->ESP32 RX2, RX->TX2, common GND (3V3-safe levels!)

[RPi gateway] --Ethernet/WiFi--> broker (mirrors heartbeat/obd topics)
[Real OBD-II dongle] --USB/BT serial--> [RPi] (ELM327 init in docs/p0-build.md §1.3)
```

## 3. Contracts (frozen)

**Verdict** (`iot/fleet/{id}/edge`):
`{risk_type, risk_score 0-1, model_version ("<bundle>+edge"|"threshold-pack-v1+esp32"),
window_end ISO, anomaly_score?, replayed bool}`.
Cloud dedups on `(device_id, window_end, source)`; `replayed:true` rows are
trusted identically (same bytes, late arrival).

**Threshold pack** (`command/edge` on ESP32; `thresholds.json` on gateway):
`{version, feature_names[12], mean[12], std[12], score_mean, score_std,
forest_threshold_sigma 2.5, forest_span_sigma 5.0, z_quiet 3.0, z_full 6.0,
risk_medium 0.4, window_points 24}`. Produced by `scripts/export_onnx.py`
from the registry bundle — single source of truth, no hand-tuned drift.

**Sensor line** (Arduino→ESP32): `{"t","h","ax","ay","az","g"}` @2Hz.

## 4. Bring-up

1. Export: `python scripts/export_onnx.py` (needs `pip install skl2onnx onnx onnxruntime`; the script asserts ONNX/sklearn parity and records the score shift).
2. Deploy pack: copy `data/models/edge/<version>/` to the gateway's
   `EDGE_PACK_DIR` (compose: `EDGE_PACK_DIR=./edge-pack.prod docker compose
   --profile edge --profile demo up -d edge`). Empty dir = gateway waits loudly.
3. Flash Arduino (`arduino/sensor_node/`), wire serial to ESP32, flash ESP32
   (`ESp32-FleetManagement/`, set `HAS_SENSORS 1` when wired, else simulated).
4. Verify: `fleet_edge_*` metrics (P-ret-2), verdict rows in logs, cloud
   `telemetry ... source=edge-mirror` absent by design (verdicts only).
5. Kill the broker for 60s: gateway spills to sqlite, replays with
   `replayed:true` on reconnect, zero verdict loss.

## 5. Limits (honest)

- ESP32 engine is thresholds, not the IF model: same features, weaker joint
  modeling. Attribution identical; sensitivity lower (documented, not hidden).
- Arduino floats are single-precision; gateway recomputes, never trusts node math.
- ONNX/TFLite on ESP32-S3 is a separate track (quantization + arena sizing);
  not started. The `+/edge` contract is forward-compatible with it.
