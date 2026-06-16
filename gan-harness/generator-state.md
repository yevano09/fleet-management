# Aegis Sprint 1 — Generator State

## What was built (Iteration 1 - initial)

### New files (9 files)
- `app/aegis/__init__.py` — Package marker
- `app/aegis/config.py` — Aegis constants and defaults
- `app/aegis/models.py` — `Remediation` + `RuleConfig` SQLAlchemy models
- `app/aegis/schemas.py` — Pydantic schemas
- `app/aegis/metrics.py` — 7 Prometheus metrics
- `app/aegis/actions.py` — Base class + 4 actions with retry/rollback
- `app/aegis/rules.py` — Rule registry + cooldown enforcement + RuleConfig merge
- `app/aegis/engine.py` — Decision engine with dry-run, per-metric signal history
- `app/aegis/scheduler.py` — Background scrape + decision loop (extracted from engine)
- `app/aegis/router.py` — REST endpoints, singleton engine injection
- `tests/test_aegis_unit.py` — 39 unit tests (rules, actions, cooldown, engine, config)

### Modified files (3 files)
- `app/config.py` — Added `aegis_dry_run` bool setting
- `app/database.py` — Imported `RuleConfig` in `init_db()`
- `app/main.py` — Uses `AegisScheduler` + `set_engine` singleton pattern

## Features implemented (Sprint 1 + Iteration 2 fixes)

### F1 — Alert Ingestion and Classification Gateway
- Background scrape loop (configurable interval) fetches `GET /metrics`
- Parses `fleet_active_devices`, `fleet_ota_in_progress`, `fleet_api_request_latency_seconds`
- Normalizes to `RemediationSignal` Pydantic model
- `POST /aegis/ingest` webhook endpoint (uses singleton engine)

### F2 — Remediation Decision Engine
- `RuleRegistry` with priority-ordered evaluation, **cooldown enforcement** (`_last_fired` dict)
- Each rule: name, condition, action_name, cooldown_seconds, max_retries, priority, enabled
- **Config-driven rule enable/disable** via `enable_rule()` + `RuleConfig` merge
- Escalation path: no match → critical Alert + Slack
- Prometheus `aegis_decisions_total{rule, decision}` counter
- `aegis_decisions_total` now tracks `cooldown` decisions separately

### F3 — Remediation Action Executor
- `RemediationAction` abstract base with `execute()`, `rollback()`
- Retry with exponential backoff (max 3 retries)
- Dead-letter queue for exhausted retries
- FIXED: gauge management is solely in `_execute_remediation` (no double-decrement)

### F4 — Built-in Actions (R001-R004)
- **R001** `throttle_ota` — Sets throttle flag + publishes MQTT `ota_resume` on rollback
- **R002** `mqtt_qos_downgrade` — Records downgraded topics + publishes `qos_restore` on rollback
- **R003** `device_soft_restart` — MQTT restart command; publishes `cancel_restart` on rollback
- **R004** `scale_heartbeat` — MQTT config publish; publishes restore on rollback

### F5 — Remediation History and Audit Trail
- `Remediation` model with full audit trail fields
- `GET /aegis/history` with pagination + filters
- `DELETE /aegis/history?older_than_days=90`

### F8 — Prometheus Metrics (Sprint 1 subset)
- All 7 metrics defined and instrumented

### F10 — Dry-Run Mode (NEW in Iteration 2)
- `AEGIS_DRY_RUN=true` env var in settings
- When true, `_execute_remediation` logs intent + records `dry_run` status
- No side effects on MQTT or actions

## Iteration 2 fixes (addressing evaluator feedback)

### Critical Issues Fixed
1. **Gauge double-decrement** — Removed all `inc()/dec()` from `execute_with_retry`; gauge is managed solely in `_execute_remediation`
2. **Cooldown enforcement** — Added `_last_fired: dict[str, datetime]` to `RuleRegistry`; `get_matching_rule()` checks cooldown before matching
3. **RuleConfig model** — Added to `models.py` with `rule_name (PK)`, `enabled`, `cooldown_seconds`, `max_retries`, `priority`, `threshold_overrides (JSON)`. Wired into `build_default_registry()` via `load_rule_configs()`/`merge_configs()`
4. **Dry-run mode** — Added `aegis_dry_run` to Settings; checked at top of `_execute_remediation`

### Major Issues Fixed
5. **Engine tests** — 7 new integration tests (classify_metrics, run_cycle, process_ingest, escalation path, signal history keys)
6. **Rollback persistence** — All rollbacks now publish MQTT messages (ota_resume, qos_restore, cancel_restart)
7. **scheduler.py** — Extracted `AegisScheduler` class from engine's `run_forever` loop
8. **Action failure-path tests** — Added `test_device_soft_restart_mqtt_disconnected`

### Minor Issues Fixed
9. **Config inconsistency** — Action timeout reads from `settings.aegis_action_timeout` with per-action override
10. **Signal history** — Uses per-metric-name key instead of per-signal-id
11. **Runtime rule enable/disable** — `enable_rule()` method + `update_rule_from_config()` for RuleConfig integration
12. **Engine singleton** — `get_engine()`/`set_engine()` in engine.py; router uses singleton
13. **Scrape URL** — Simplified to single `/metrics` attempt

## Test results
- **39 Aegis unit tests: all passing** (was 23 in Iteration 1, +16 new)
- **1 config unit test: passing**
- **0 regressions** — all existing tests unchanged

## Configurable env vars
| Variable | Default | Description |
|---|---|---|
| `AEGIS_SCRAPE_INTERVAL` | 15 | Scrape loop interval in seconds |
| `AEGIS_ACTION_TIMEOUT` | 30 | Action execution timeout |
| `AEGIS_RETRY_MAX` | 3 | Max retries per action |
| `AEGIS_ACTIVE_DEVICES_THRESHOLD` | 2.0 | Min active devices before pressure |
| `AEGIS_OTA_IN_PROGRESS_THRESHOLD` | 3.0 | Max OTA deployments before pressure |
| `AEGIS_LATENCY_THRESHOLD` | 0.5 | API latency threshold in seconds |
| `AEGIS_OFFLINE_RATIO_THRESHOLD` | 0.3 | Offline ratio threshold |
| `AEGIS_DRY_RUN` | False | Dry-run mode (log only, no side effects) |
