# Evaluation — Iteration 001

## Scores

| Criterion | Sub-score | Weight | Weighted |
|-----------|-----------|--------|----------|
| 1.1 Visual Hierarchy | 7/10 | 0.25 of Design | |
| 1.2 Color & Typography | 3/10 | 0.25 of Design | |
| 1.3 Motion & Feedback | 3/10 | 0.20 of Design | |
| 1.4 Consistency & Integration | 8/10 | 0.30 of Design | |
| **Design Quality** | **5.5/10 (avg)** | **0.3** | **1.65** |
| 2.1 Beyond Passive Alerts | 6/10 | 0.35 of Orig | |
| 2.2 IoT-Specific Actions | 7/10 | 0.40 of Orig | |
| 2.3 No-ML Learning | 1/10 | 0.25 of Orig | |
| **Originality** | **4.7/10 (avg)** | **0.2** | **0.94** |
| 3.1 Error Handling & Resilience | 6/10 | 0.30 of Craft | |
| 3.2 Testing Coverage | 6/10 | 0.25 of Craft | |
| 3.3 Observability | 7/10 | 0.25 of Craft | |
| 3.4 Idempotency & Safety | 5/10 | 0.20 of Craft | |
| **Craft** | **6.0/10 (avg)** | **0.3** | **1.80** |
| 4.1 Alert Coverage | 6/10 | 0.25 of Func | |
| 4.2 Action Depth | 5/10 | 0.30 of Func | |
| 4.3 Integration Surface | 5/10 | 0.25 of Func | |
| 4.4 Safety & Governance | 4/10 | 0.20 of Func | |
| **Functionality** | **5.0/10 (avg)** | **0.2** | **1.00** |
| **TOTAL** | | **1.0** | **5.39/10** |

### Grade: C — Below bar. Needs significant work.

---

## Must-Pass Gate Results

| Gate | Result | Notes |
|------|--------|-------|
| 3.1 — Retry + rollback on ≥4 actions | PASS | All 4 actions have retry via base class + rollback defined |
| 3.2 — Unit tests for ≥6 actions | FAIL | Only 4 actions exist (Sprint 1 scope); all 4 have tests. Gate assumes 6+ actions |
| 4.1 — ≥3 distinct `fleet_*` metrics | PASS | active_devices, ota_in_progress, api_request_latency |
| 4.4 — Dry-run mode + human escalation | **FAIL** | Human escalation ✓. Dry-run mode ✗ (not implemented) |

---

## Verdict: FAIL (below 6.0 threshold, dry-run gate failed)

---

## Critical Issues (must fix)

1. **Gauge double-decrement bug** (`app/aegis/actions.py:47-66`). `aegis_active_remediations.dec()` is called in BOTH the `except` block (line 57) AND the `finally` block (line 66). On action failure, the gauge decrements twice per attempt while only incrementing once. Additionally, `engine.py:218` also calls `.inc()` and `engine.py:250` calls `.dec()`, so failures in `execute_with_retry` combine with `_execute_remediation`'s own inc/dec to produce a net -2 per failed action. After one failed action, the gauge reads −1 instead of 0.

2. **Cooldown fields defined but NEVER enforced** (`app/aegis/rules.py:18`). Every rule has a `cooldown_seconds` field, and `build_default_registry()` sets values (300, 600, 900s). But `RuleRegistry.get_matching_rule()` (line 49) never checks cooldown — it only checks `rule.matches(signal)`. This means a rule can fire every 15-second scrape cycle with no throttling. Cooldown is decorative code.

3. **Missing `RuleConfig` model** (`app/aegis/models.py`). The Sprint 1 deliverables in `spec.md:384` explicitly list `RuleConfig` model and `rule_configs` table. Neither exists. There is no way to persist or modify rule configuration (enable/disable, thresholds, cooldowns) at runtime without code changes.

4. **Missing dry-run mode**. The must-pass gate 4.4 requires dry-run mode AND human escalation. Human escalation exists; dry-run mode does not. No `AEGIS_DRY_RUN` env var, no dry-run status path, no dry-run filter in history.

---

## Major Issues (should fix)

5. **No engine tests** (`tests/test_aegis_unit.py`). Sprint 1 deliverables specify "Unit tests for engine + rules + first 4 actions." The tests cover rules (10 tests) and actions (10 tests) but have zero tests for `AegisEngine` — no test for `run_cycle`, `_classify_metrics`, `_execute_remediation`, `_escalate_human`, or `process_ingest`. This is a significant coverage gap.

6. **Rollback implementations are mostly no-ops**. `DeviceSoftRestartAction.rollback()` at line 166 just logs "cannot unsend restart command" and returns `True`. This is an honest admission but violates the spec requirement for a proper rollback mechanism. `ThrottleOtaAction.rollback()` and `MqttQosDowngradeAction.rollback()` modify in-memory state only — there's no persistence or reversal of MQTT effects.

7. **Missing `app/aegis/scheduler.py`**. The spec file listing (line 319) requires a `scheduler.py` for the background scrape + decision loop. The current implementation embeds the loop in `AegisEngine.run_forever()` inside `engine.py`. While functional, this deviates from the spec structure and makes the scheduler non-reusable.

8. **Actions only test success path**. All 10 action tests exercise only happy-path execution. The only failure test (`test_execute_with_retry_exhaustion`) tests the generic retry wrapper, not any action's specific failure mode. No test validates what happens when MQTT publish fails on `device_soft_restart` (the code does handle it, but it's untested).

---

## Minor Issues (nice to fix)

9. **Config inconsistency**: `config.py` uses module-level constants (`AEGIS_DEFAULT_ACTION_TIMEOUT`, `AEGIS_RETRY_MAX`), while `engine.py` reads scrape interval from `settings.aegis_scrape_interval`. Action timeout is read from the module constant, not from `settings.aegis_action_timeout`. This means changing `AEGIS_ACTION_TIMEOUT` via `.env` has no effect on action execution.

10. **Signal history uses wrong key**. `engine.py:147` uses `sig_id` (a freshly-generated UUID) as the key for `self._signal_history`, then appends exactly 1 value. Each scrape cycle generates a new UUID, so history is always exactly 1 entry. The spec calls for "sparkline of metric trend (last 5 values)" which requires per-metric-name history, not per-signal-id.

11. **No runtime rule enable/disable**. Rules have an `enabled` field and `matches()` checks it, but there's no API to toggle it and no `RuleConfig` table to persist it. Rules can only be enabled/disabled in source code.

12. **`POST /aegis/ingest` creates a new engine instance** (`router.py:100-101`). Each webhook call creates a fresh `AegisEngine` with `build_default_registry()`. This is wasteful — the engine instance should be a singleton injected via dependency or lifespan.

13. **Backend metrics scrape uses `requests` sync**. `engine.py:84` wraps `requests.get` in `asyncio.to_thread`. This works but the existing project uses `httpx` nowhere — however, `requests` is already a dependency. The sync-wrapped-in-async pattern is fine but the in-container URL fallback logic on line 87 (`/metrics` → `/metrics/`) is fragile.

---

## Specific Suggestions

1. **Fix the gauge**: Remove the `inc()`/`dec()` calls from `execute_with_retry` and let `_execute_remediation` be the sole owner of `aegis_active_remediations` management.

2. **Enforce cooldowns**: Add a `_last_fired: dict[str, datetime]` to `RuleRegistry` and check cooldown in `get_matching_rule()` before matching.

3. **Add `RuleConfig` model**: Simple model with `rule_name (PK)`, `enabled`, `cooldown_seconds`, `max_retries`, `priority`, `threshold_overrides (JSON)`. Wire into `build_default_registry()` to merge config overrides.

4. **Add a basic engine integration test**: Even without Docker, an engine test with mocked `_scrape_metrics()` that returns known Prometheus text is feasible and valuable.

5. **Add dry-run mode**: `AEGIS_DRY_RUN` env var checked at the top of `_execute_remediation`. When true, run decision engine + log output but skip actual action execution; set status to `"dry_run"` instead.

6. **Add at least one action failure-path test**: Test `device_soft_restart` with `mqtt_client.is_connected = False` to validate the failure branch produces `success=False` and populates `devices_failed`.

7. **Remove duplicate inc/dec in `engine.py:218,250`** and keep the tracking solely in `execute_with_retry`, OR remove from `execute_with_retry` and keep in `_execute_remediation` — pick one strategy.

---

## What's Good

- **Module structure is clean and follows project patterns**. The nine new files in `app/aegis/` each have a single responsibility, matching the existing codebase's conventions exactly.
- **All 7 Prometheus metrics are defined and instrumented** — exceeding the Sprint 1 F8 requirement of 3 metrics. This is pro-active overdelivery.
- **Dead-letter queue mechanism exists**. Actions that exhaust retries go to `dlq` status, `aegis_dlq_depth` gauge is incremented, and the mechanism is visible in the API.
- **Human escalation path** integrates with the existing alert engine + Slack notification pipeline. This is proper reuse.
- **All 23 unit tests pass** cleanly with no warnings, and the full existing 63-test suite also passes.
- **Idempotent action design**: `throttle_ota`, `mqtt_qos_downgrade`, and `scale_heartbeat` all track state correctly and produce the same result on re-execution.
- **Proper async patterns**: `asyncio.wait_for` timeout enforcement, `asyncio.sleep` for backoff, `async_session_factory` for DB sessions, lifespan management with stop/cancel on shutdown.
- **Config-driven thresholds**: All thresholds (active devices, OTA in progress, latency, offline ratio) are configurable via env vars.
- **Webhook ingestion endpoint** (`POST /aegis/ingest`) enables integration with external Prometheus Alertmanager — a nice future-proofing touch.
