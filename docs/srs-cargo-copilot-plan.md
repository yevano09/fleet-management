# Implementation Plan — Smart Cargo Monitoring + Fleet Agentic Copilot

Maps the SRS (Idea 4: Smart Cargo & Environmental Monitoring, Idea 5: Fleet
Agentic Copilot) onto this repo's conventions. Honesty rule from day one: every
"AI" claim ships with an eval gate and a heuristic fallback, exactly like
ML-01/EVAL-01 did. Nothing here requires scrapping existing systems — both
modules extend the MQTT → backend → dashboard → twin loop.

Conventions reused: role ranks (`app/deps.py`), org tenancy (`org_id` on new
tables), `fleet_*` Prometheus metrics + Grafana panels, e2e in
`tests/test_e2e.py` (live-HTTP), seeded eval in `tests/test_ml_eval.py` style,
docs ship with code, demo script beats in `DEMO_GUIDE.md`.

---

## 0. SRS → repo mapping (what exists vs what's new)

| SRS element | Repo today | Plan |
|---|---|---|
| Edge sensors / OBD-II MQTT/TLS | Simulator heartbeat/obd/bms (QoS1, mTLS prod profile) | Extend simulator with cargo profiles (temp/humidity/door/IMU); new `+/cargo` topic |
| Cold-chain / shock edge models (TFLite) | None (ESP32 stays thin publisher) | **Simulated edge first**: verdicts published by simulator on `+/cargo`; real TFLite later, same topic contract |
| Telemetry streamer | Batch queue (200 rows/1s), tenant-stamped | New `cargo_readings` hypertable-ready table + 5m rollups via retention worker pattern |
| FastAPI + TimescaleDB backend | FastAPI + Postgres/SQLite, Alembic, 7d retention | Reuse; add cargo tables via Alembic revision |
| Vector DB (Chroma/Qdrant) | None | **Phase 1: Postgres FTS (`tsvector`)** on ingested manuals — zero new infra; Qdrant only if FTS proves insufficient |
| LangChain/LlamaIndex agent core | CrewAI opt-in + deterministic `agents/` tools | New `agents/copilot.py` toolset over existing `async_tools`; LLM provider abstraction with mock default |
| Dashboard APIs | `GET /twin/{id}`, alerts, schedules | Cargo panel + twin `cargo` block; `POST /agents/copilot/chat` |

## 1. Module 1 — Smart Cargo & Environmental Monitoring

### M1.1 Cargo data plane (S, ~1 wk)
1. `app/models.py`: `CargoProfile` (device_id FK, commodity, temp_min/max, thermal_mass, door_alerts bool, org_id) + `CargoReading` (device_id, ts, bay_temp, humidity, door_open, shock_g, source, tenant_id) + `ShockEvent` (device_id, ts, peak_g, axis, class, org_id). Alembic revision `0003_cargo`.
2. `app/mqtt_client.py`: subscribe `iot/fleet/+/cargo`; `handle_mqtt_cargo` → validate → batch queue (reuse `enqueue_telemetry` pattern with a cargo builder) → `CargoReading` rows.
3. Simulator: `--cargo` profile (SHT30-ish temp/humidity drift, door events, IMU random walk + scriptable `HARD_DROP` injection mirroring `SIMULATOR_SCENARIO` flags); publishes `+/cargo` with `ai_inference` stub block matching the SRS payload shape.
4. `GET /cargo/{device_id}/readings?hours=&limit=`, `GET /cargo/{device_id}/profile`, `PUT` profile (operator). e2e test_46.
5. Exit: live `+/cargo` frames visible in DB + API; simulator demo shows bay temp/humidity/door in dashboard device detail (OBD line extended).

### M1.2 Cold-chain TTS + reroute (M, ~2 wks)
1. `app/cargo_thermal.py`: TTS estimator v1 = slope extrapolation on 15-min bay-temp window vs threshold (same honesty grade as legacy slopes) + door-open penalty; outputs `{tts_minutes, risk_level}`.
2. Alert type `cargo_spoilage_risk` (critical when TTS < remaining-trip buffer; needs trip ETA field on CargoProfile) → AlertEngine (dedup/cooldown free) → reroute recommendation text (nearest-depot lookup stubbed by geofence list; real routing is P1).
3. 3-sample median smoothing on intake (SRS failure mode) + sensor-stale fallback (last-known decay curve flagged `stale:true`).
4. Twin `cargo` block: current bay temp, TTS, active shock count, door state. Dashboard Twin tab renders it.
5. Eval: seeded thermal scenarios (extend `app/ml/scenarios.py` with cargo profiles) asserting TTS error bounds + FP gate in `tests/test_ml_eval.py` style.
6. Exit: demo — warm a simulated bay past 4°C, watch TTS countdown → critical alert → WO auto-open → twin shows risk.

### M1.3 Shock classification (M, ~2 wks, simulated edge first)
1. `ShockEvent` pipeline: 100 Hz IMU windows (simulated) → v1 heuristic classifier (peak-g thresholds + duration → the 4 SRS classes) behind the same candidate-dict contract as `ml_score_points`.
2. Edge contract: verdicts on `iot/fleet/{id}/edge` with `{event: HARD_DROP, peak_g, axes, model_version: heuristic-v1}` — identical shape the future TFLite model will publish; backend can't tell the difference (that's the point).
3. 1D-CNN training track (needs labeled drops — collect via M1.3 heuristic logs as weak labels; promote only on beating heuristic precision in eval).
4. Exit: drop injection → classified event → alert + twin entry, all timestamped <1 beat after injection.

## 2. Module 2 — Fleet Agentic Copilot

### M2.1 Tool layer + chat API with mock brain (M, ~2 wks)
1. `agents/copilot_tools.py` (async, DB-backed like `async_tools.py`): `get_active_dtc_codes`, `get_vehicle_telemetry`, `get_twin_summary`, `list_open_alerts`, `get_work_orders` — all parameterized, **no raw SQL from callers** (SRS guardrail satisfied structurally, not by prompt).
2. `app/models.py`: `CopilotSession` (id, user, role, org_id, created) + `CopilotMessage` (session FK, role, content, tools_json).
3. `POST /agents/copilot/chat {message, session_id?}` (operator): rule-based dispatcher v1 — intent regexes (`dtc|engine light`, `hard braking|offenders?`, `health|status of`) → tools → templated answers + `actions_taken` + `suggested_actions` in the SRS response shape. Deterministic, testable, no API keys.
4. RAG-lite: `service_notes` FTS table (`tsvector` on title/body, seeded from `docs/` runbooks + DTC meanings from `app/obd/dtc.py`); `search_service_notes(query)` ranks by FTS, cited in answers.
5. e2e test_47: "why is engine light on" → response mentions DTC + telemetry; offender query → table + memo draft.
6. Exit: demo-able copilot over HTTP with zero external dependencies; every answer cites tool outputs.

### M2.2 Real LLM + vector upgrade (M–L, gated)
1. `agents/llm_provider.py`: interface `{complete(messages, tools)}` with `mock` (default, replays M2.1 templates), `openai`/`anthropic` (keys via env, never logged), `vllm` (local endpoint). Strict mode refuses to boot with a non-mock provider and no key (fail-closed, like `validate_settings`).
2. Tool-calling via provider-native function calling; system prompt pins: read-only tools only, must cite tool outputs, escalation phrases for safety-critical advice ("pull over", "do not drive" require DTC+telemetry evidence present).
3. RAG graduation gate: if FTS answer-recall on a seeded Q/A set (20 questions, checked into `tests/copilot_qa.json`) < 80%, THEN add Qdrant + embeddings; else stay on FTS (YAGNI enforced by metric).
4. Latency budget: p95 < 2.5s first-token (SRS NFR) measured in eval; stream via SSE if needed (P1).
5. Exit: same e2e suite passes with `LLM_PROVIDER=openai|mock` matrix in CI (mock always, live keyed nightly).

## 3. Shared requirements (both modules)

- **Metrics**: `fleet_cargo_readings_total{source}`, `fleet_cargo_alerts_total{type}`, `fleet_copilot_requests_total{intent,provider}`, `fleet_copilot_latency_seconds`, `fleet_copilot_tool_calls_total{tool}`; Grafana: cargo bay temps + TTS panel, copilot latency panel.
- **Security**: copilot requires operator+; sessions scoped to caller org; tool layer allowlists columns (no `SELECT *`); SQL tool takes structured args, never raw strings; LLM keys env-only; PII (driver names) redacted unless role ≥ fleet_manager.
- **Docs**: `USE_CASES.md` UC-33 (cargo), UC-34 (copilot); `DEMO_GUIDE.md` §15 beats; `docs/p0-build.md` log rows.
- **NFR mapping**: edge inference <50ms → measured on simulator verdict path now, real TFLite later with same harness; offline SPIFFS/SD spill → simulator-equivalent replay test via broker disconnect; TLS 1.3 → existing mTLS profile (document cipher floor).

## 4. Sequencing (~7–9 wks, single engineer)

```
M1.1 (cargo plane) ──▶ M1.2 (TTS+reroute) ──▶ M1.3 (shock)
M2.1 (tools+mock chat, needs M1.1 readings + twin) ──▶ M2.2 (LLM, gated)
Shared metrics/docs ride each milestone; eval gates block every promotion.
```

## 5. First acceptance demo (end of M1.2+M2.1)

"TRUCK-104's bay hits 6.2°C → TTS 42 min → critical alert → WO auto-opens → ask copilot *'which vehicles breach cold-chain in the next hour?'* → answers TRUCK-104 with tool citations → twin shows the whole story." That single narrative exercises every SRS use case except on-device TFLite (contract-ready stub).
