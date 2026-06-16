# Evaluation — Iteration 002

## Scores

| Criterion | Sub-score | Weight | Weighted |
|-----------|-----------|--------|----------|
| 1.1 Visual Hierarchy | 7/10 | 0.25 of Design | |
| 1.2 Color & Typography | 3/10 | 0.25 of Design | |
| 1.3 Motion & Feedback | 3/10 | 0.20 of Design | |
| 1.4 Consistency & Integration | 8/10 | 0.30 of Design | |
| **Design Quality** | **5.25/10 (avg)** | **0.3** | **1.58** |
| 2.1 Beyond Passive Alerts | 6/10 | 0.35 of Orig | |
| 2.2 IoT-Specific Actions | 7/10 | 0.40 of Orig | |
| 2.3 No-ML Learning | 1/10 | 0.25 of Orig | |
| **Originality** | **5.15/10 (avg)** | **0.2** | **1.03** |
| 3.1 Error Handling & Resilience | 8/10 | 0.30 of Craft | |
| 3.2 Testing Coverage | 6/10 | 0.25 of Craft | |
| 3.3 Observability | 7/10 | 0.25 of Craft | |
| 3.4 Idempotency & Safety | 7/10 | 0.20 of Craft | |
| **Craft** | **7.05/10 (avg)** | **0.3** | **2.12** |
| 4.1 Alert Coverage | 6/10 | 0.25 of Func | |
| 4.2 Action Depth | 5/10 | 0.30 of Func | |
| 4.3 Integration Surface | 5/10 | 0.25 of Func | |
| 4.4 Safety & Governance | 8/10 | 0.20 of Func | |
| **Functionality** | **5.85/10 (avg)** | **0.2** | **1.17** |
| **TOTAL** | | **1.0** | **5.90/10** |

### Grade: C — Below bar, but significantly improved.

---

## Must-Pass Gate Results

| Gate | Result | Notes |
|------|--------|-------|
| 3.1 — Retry + rollback on ≥4 actions | **PASS** | All 4 actions have retry via base class `execute_with_retry` + rollback with MQTT messages |
| 3.2 — Unit tests for ≥6 actions | **FAIL** | Only 4 actions exist (Sprint 1 scope). Tests exist for all 4 with execute + rollback + 1 failure path + engine integration tests (39 Aegis tests total). Gate still requires ≥6 actions. |
| 4.1 — ≥3 distinct `fleet_*` metrics | **PASS** | `fleet_active_devices`, `fleet_ota_in_progress`, `fleet_api_request_latency_seconds` via histogram parsing |
| 4.4 — Dry-run mode + human escalation | **PASS** | Both implemented. `AEGIS_DRY_RUN` env var → `settings.aegis_dry_run` → dry_run path in `_execute_remediation`. Human escalation via `_escalate_human` → AlertEngine + Slack. |

---

## Verdict: FAIL (below 7.0 threshold)

---

## Critical Issues (from feedback-001) — Fix Verification

### 1. Gauge double-decrement — ✅ **FIXED**

`aegis_active_remediations` management is now solely in `engine.py:_execute_remediation`:
- `inc()` on line 188 (one inc per remediation attempt)
- `dec()` on line 198 (dry-run path, net 0)
- `dec()` on line 233 (normal path, net 0)

`actions.py:execute_with_retry` has zero `inc()`/`dec()` calls. No double-decrement path exists.

⚠️ **New minor issue**: `aegis_remediations_total` is now **double-incremented** for non-exception failure returns. When `action.execute()` returns `RemediationResult(success=False)` (no exception — e.g. MQTT disconnected), `execute_with_retry` increments the counter with status="failed" (line 53) AND `_execute_remediation` increments again (line 234). For DLQ exhaustion, it's N+1 increments (N failed attempts + 1 dlq). This is less harmful than the gauge bug but inflates failure counts.

### 2. Cooldown enforcement — ✅ **FIXED**

`rules.py:RuleRegistry` now has `_last_fired: dict[str, datetime]` (line 40). `get_matching_rule()` checks cooldown at lines 81-88: if `_last_fired[rule.name]` is within `rule.cooldown_seconds`, it logs and continues to next rule. `aegis_decisions_total` tracks `decision="cooldown"` separately. Two unit tests verify enforcement and expiry.

### 3. Missing `RuleConfig` model — ✅ **FIXED**

`models.py:33-57` defines `RuleConfig` with `rule_name (PK)`, `enabled`, `cooldown_seconds`, `max_retries`, `priority`, `threshold_overrides (JSON)`. Imported in `database.py:25` for `init_db()`. Wired into rules via `load_rule_configs()`/`merge_configs()` in `rules.py`. Three unit tests validate defaults, JSON parsing, and malformed JSON handling.

### 4. Missing dry-run mode — ✅ **FIXED**

`app/config.py:66` adds `aegis_dry_run: bool = False`. `engine.py:_execute_remediation` lines 190-200 check `getattr(settings, 'aegis_dry_run', False)`. When True: logs intent, sets `status = "dry_run"`, records full input/output snapshots, skips action execution, and decrements the gauge. The `AEGIS_DRY_RUN` env var is documented in generator-state.md.

---

## Major Issues (from feedback-001) — Fix Verification

### 5. No engine tests — ✅ **FIXED**

11 new engine-related tests added:
- `TestEngineMetricsParsing` (4 tests): classify_metrics, non-fleet filtering, comment skipping, signal history keys
- `TestEngineFullCycle` (4 tests): run_cycle with/without metrics, process_ingest, escalation path
- `TestRuleConfigModel` (3 tests): defaults, threshold_overrides, bad JSON

### 6. Rollback implementations — ✅ **FIXED**

All 4 actions now publish real MQTT messages on rollback:
- `ThrottleOtaAction`: publishes `iot/fleet/command/ota_resume` with JSON payload
- `MqttQosDowngradeAction`: publishes `iot/fleet/command/qos_restore` with topic list
- `DeviceSoftRestartAction`: publishes per-device `iot/fleet/{id}/command/cancel_restart`
- `ScaleHeartbeatAction`: publishes per-device config restore with original interval

⚠️ All rollbacks return `True` even when MQTT is disconnected (no exception on failed publish). This is defensible but means rollback failures are silently swallowed.

### 7. Missing `scheduler.py` — ✅ **FIXED**

`app/aegis/scheduler.py` exists with `AegisScheduler` class: `run()` with async loop, `stop()` flag, interval configured from `settings.aegis_scrape_interval`, CancelledError handling, exception logging per cycle.

### 8. Actions only test success path — ✅ **PARTIALLY FIXED**

`test_device_soft_restart_mqtt_disconnected` tests failure with `mqtt_client.is_connected = False`, validates `success=False` and `devices_failed` contains 2 devices. The generic `test_execute_with_retry_exhaustion` tests retry exhaustion.

Still missing: failure-path tests for throttle_ota, mqtt_qos_downgrade, scale_heartbeat. Only 1 of 4 actions has a dedicated failure test.

---

## Minor Issues (from feedback-001) — Fix Verification

### 9. Config inconsistency — ✅ **FIXED**

`engine.py:203` reads `getattr(action, 'timeout', settings.aegis_action_timeout) or settings.aegis_action_timeout`. Action timeout falls back to settings with per-action override. All thresholds read from `settings.*` via `getattr()`.

### 10. Signal history key — ✅ **FIXED**

`engine.py:_classify_metrics` uses per-metric-name keys: `"fleet_active_devices"`, `"fleet_ota_in_progress"`, histogram key for latency. Test `test_signal_history_uses_metric_name_key` validates this.

### 11. Runtime rule enable/disable — ✅ **FIXED**

`rules.py:55-59` `enable_rule(name, enabled)` toggles rule state. `update_rule_from_config()` at line 62 merges RuleConfig into a rule. Tests in `TestRuleEnableDisable` verify toggle works and unknown rules return False.

### 12. Engine singleton — ✅ **FIXED**

`engine.py:315-327`: `get_engine()`/`set_engine()` singleton pattern. `router.py:98` calls `get_engine()`. `main.py:117` calls `set_engine(aegis_engine)` on startup. No more per-request engine creation.

### 13. Scrape URL — ✅ **ACKNOWLEDGED** (unchanged, minor by admission)

---

## New Issues Found

1. **`aegis_remediations_total` double-count**: `execute_with_retry` (line 53) and `_execute_remediation` (line 234) both increment `aegis_remediations_total` for the same action result. For non-DLQ failures, this means 2× `failed` increments. For DLQ, it's N failed-attempt increments + 1 dlq increment. Fix: remove the increment from `execute_with_retry` and let `_execute_remediation` be the sole owner.

2. **Dry-run only at action level, not cycle level**: The `AEGIS_DRY_RUN` check is inside `_execute_remediation`. The scrape, classify, and decision phases run normally even in dry-run. The spec (F10) expects dry-run to produce identical decision output — which it does — but there's no way to run a full dry-run cycle from ingestion to log without any side effects (the `run_cycle` method still does everything except execute actions). This is mostly correct but worth noting.

3. **No `POST /aegis/scan` endpoint**: The spec F7 calls for `GET /aegis/scan` and `GET /aegis/history` in the router. Only history endpoints exist. No way to trigger an on-demand scan via API.

---

## What Improved

- **All 4 critical issues fixed** — gauge, cooldown, RuleConfig, dry-run mode. These were structural blockers in iteration 1.
- **Rollbacks went from no-ops to real MQTT messages** — each action now publishes a meaningful rollback command.
- **39 tests (was 23)** — 16 new tests including engine integration, failure path, RuleConfig model.
- **Engine singleton** — no more per-request engine instantiation in the webhook path.
- **scheduler.py extracted** — matches spec file listing.
- **Signal history uses correct keys** — per-metric-name, enabling sparkline trend data.

---

## What's Still Missing

- **Dashboard panel** (F6) — no UI at all. Design Quality scores unchanged because no front-end work was done.
- **CLI flags** (F7) — no `--remediate` or `--remediation-history` in `run_agents.py`.
- **Actions R005-R008** (F4) — still only 4 actions; R005 rollback_ota_batch, R006 human_escalation (as action), R007 migrate_device_pool, R008 cleanup_firmware_artifacts not implemented. These would unblock 3.2 gate.
- **No learning** (F9) — `RuleWeight` model and Bayesian adjustment not implemented (Sprint 3 scope).
- **No Grafana dashboard** (F16) — Sprint 4 scope, but would boost observability score.
- **`METRICS.md`** — 7 Prometheus metrics exist but are undocumented.

---

## Summary

This iteration successfully addressed all critical and major issues from feedback-001. The codebase is structurally sound, the gauge bug is eliminated, dry-run mode is functional, and engine tests now exist. The 39 passing tests provide reasonable confidence.

The score remains below threshold (5.90) primarily due to Sprint 1 scope limitations (4 of 8 actions, no UI, no CLI) and unchanged Design/Originality scores. The Craft sub-score improved significantly from 6.0 → 7.05.

**Next priorities for passing the 7.0 threshold**: Implement remaining 4 actions (R005–R008) to unblock 3.2 gate, add a basic dashboard panel to improve Design scores, and fix the `aegis_remediations_total` double-count.
