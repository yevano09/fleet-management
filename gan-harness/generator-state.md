# Aegis Sprint 2 — Generator State

## What was built (Iteration 3 — Sprint 2 Integration)

### Modified files (12 files)
- `app/aegis/actions.py` — Added R005-R008 actions, fixed double-count bug in `execute_with_retry`
- `app/aegis/rules.py` — Added rules r005 through r008
- `app/aegis/router.py` — Added `GET /aegis/scan`, `GET /aegis/summary`
- `agents/async_tools.py` — Added `async_detect_resource_pressure`, `async_run_remediation_cycle`, `async_get_remediation_history`
- `agents/tools.py` — Added `detect_resource_pressure`, `run_remediation_cycle`, `get_remediation_history`, `rerun_remediation`
- `agents/phase1_crew.py` — Added `run_remediation_agent()`
- `agents/routers.py` — Added `GET /aegis/scan`, `GET /aegis/history`, `POST /aegis/rerun/{id}`
- `run_agents.py` — Added `--remediate`, `--remediation-history`, `--remediation-rerun <id>` flags
- `app/templates/dashboard.html` — Added Aegis three-column remediation panel with HTMX auto-refresh
- `tests/test_aegis_unit.py` — Added 12 new tests for R005-R008 actions, updated registry test to 8 rules
- `gan-harness/generator-state.md` — Updated to Sprint 2 state

## Features implemented (Sprint 2)

### F4 — All 8 Actions (R001-R008)
- **R005** `rollback_ota_batch` — Roll back OTA to `previous_firmware_version` for affected devices; creates Alert via AlertEngine; publishes MQTT `command/rollback`
- **R006** `human_escalation` — Creates critical Alert via AlertEngine with full signal trace; auto-assigns to on-call
- **R007** `migrate_device_pool` — Publishes MQTT `command/maintenance` with `enter_maintenance`/`exit_maintenance`; rollback restores devices
- **R008** `cleanup_firmware_artifacts` — Deletes oldest resolved OTA artifacts from storage; logs freed space in MB

### F6 — Dashboard Remediation Panel
- Three-column layout between alerts and device table
- Left column: last 10 signals with severity badges (critical/warning/info)
- Center column: active remediations with animated pulse indicator (CSS `@keyframes aegis-pulse`)
- Right column: last 20 history entries as timeline with green/amber/red/purple dots
- Top summary bar: "X auto-resolved / Y escalated / Z pending"
- Auto-refresh every 10s (setInterval) alongside existing dashboard intervals
- Inline expandable entries with JSON input/output snapshots (click to toggle)

### F7 — REST + CLI Agent Integration
- `agents/async_tools.py`: `async_detect_resource_pressure(db)`, `async_run_remediation_cycle(db)`, `async_get_remediation_history(db, ...)`
- `agents/tools.py`: `detect_resource_pressure()`, `run_remediation_cycle()`, `get_remediation_history()`, `rerun_remediation(id)`
- `agents/phase1_crew.py`: `run_remediation_agent()` following agent dict pattern
- `agents/routers.py`: `GET /agents/aegis/scan`, `GET /agents/aegis/history`, `POST /agents/aegis/rerun/{id}`
- `run_agents.py`: `--remediate`, `--remediation-history`, `--remediation-rerun <id>` flags

### F10 — Dry-Run Mode (carried forward from Iteration 2)

## Fixed Issues
1. **`aegis_remediations_total` double-count** — Removed both increment calls from `actions.py:execute_with_retry`. Counter is now owned solely by `engine.py:_execute_remediation` (line 234 for normal path, line 199 for dry_run path).

## Unit Test Count
- **51 Aegis unit tests** (was 39 in Iteration 2, +12 new for R005-R008 actions)
- Tests cover success path, failure path (MQTT disconnected), rollback, status() methods, and registry for all new actions

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
