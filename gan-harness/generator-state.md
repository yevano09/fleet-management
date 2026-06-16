# Aegis Sprint 1 — Generator State

## What was built

### New files (9 files)
- `app/aegis/__init__.py` — Package marker
- `app/aegis/config.py` — Aegis constants and defaults (scrape interval, timeouts, retries)
- `app/aegis/models.py` — `Remediation` SQLAlchemy model with full audit trail fields
- `app/aegis/schemas.py` — Pydantic schemas: `RemediationSignal`, `RemediationResponse`, `RemediationListResponse`, `IngestRequest`
- `app/aegis/metrics.py` — 7 Prometheus metrics per F8 spec (aegis_signals_total, aegis_decisions_total, aegis_scrape_duration_seconds, aegis_remediations_total, aegis_remediation_duration_seconds, aegis_dlq_depth, aegis_active_remediations)
- `app/aegis/actions.py` — Base `RemediationAction` class + 4 concrete implementations (R001-R004) with execute/rollback, retry with exponential backoff, timeout enforcement, DLQ
- `app/aegis/rules.py` — `RemediationRule` + `RuleRegistry` with priority-ordered evaluation, enable/disable toggle, 4 built-in rules mapped to R001-R004
- `app/aegis/engine.py` — `AegisEngine` with background scrape loop, metric classification, rule matching, action execution, human escalation path, webhook ingest support
- `app/aegis/router.py` — REST endpoints: `GET /aegis/history` (paginated/filtered), `GET /aegis/history/{id}`, `POST /aegis/ingest`, `DELETE /aegis/history`
- `tests/test_aegis_unit.py` — 23 unit tests covering rule registry (10), actions with MQTT mock (10), signal model (3)

### Modified files (3 files)
- `app/config.py` — Added 8 Aegis configuration fields (scrape interval, timeouts, thresholds) with env var support; auto-sets backend URL using internal port
- `app/database.py` — Imported `app.aegis.models.Remediation` in `init_db()` for table creation
- `app/main.py` — Included `aegis_router`, started Aegis background task in lifespan with stop/cancel on shutdown

## Features implemented (Sprint 1)

### F1 — Alert Ingestion and Classification Gateway
- Background scrape loop (configurable `AEGIS_SCRAPE_INTERVAL`, default 15s) fetches `GET /metrics/` 
- Parses `fleet_active_devices`, `fleet_ota_in_progress`, `fleet_api_request_latency_seconds` metrics
- Normalizes to `RemediationSignal` Pydantic model with all required fields
- `POST /aegis/ingest` webhook endpoint for Alertmanager integration
- Emits `aegis_signals_total{severity, metric}` counter

### F2 — Remediation Decision Engine
- `RuleRegistry` with priority-ordered rule evaluation (first match wins)
- Each rule has: name, condition, action_name, cooldown_seconds, max_retries, priority, enabled
- Config-driven enable/disable per rule
- Escalation path: no match → creates critical Alert via AlertEngine + records escalated remediation
- Emits `aegis_decisions_total{rule, decision}` counter

### F3 — Remediation Action Executor
- `RemediationAction` abstract base with `execute(signal, context)` and `rollback(signal, context)`
- Timeout enforcement via `asyncio.wait_for` (default 30s, configurable)
- Retry with exponential backoff (max 3 retries, configurable)
- Dead-letter queue: actions that exhaust retries go to `dlq` state; `aegis_dlq_depth` gauge incremented
- All results persisted to DB with full input/output snapshots

### F4 — Built-in Actions (R001-R004)
- **R001** `throttle_ota` — Sets throttle flag when OTA in progress > threshold
- **R002** `mqtt_qos_downgrade` — Records which non-critical topics would be downgraded to QoS 0
- **R003** `device_soft_restart` — Publishes MQTT restart command to affected devices
- **R004** `scale_heartbeat` — Increases heartbeat frequency to 5s for remaining online devices
- All actions have execute + rollback, are idempotent on re-run

### F5 — Remediation History and Audit Trail
- `Remediation` model with all required fields (id, signal_id, metric_name, value, threshold, severity, rule_name, action_name, status, input/output snapshots, error_message, timestamps, duration_ms, retry_count, device_ids)
- `GET /aegis/history` with pagination + filters (status, action, metric)
- `GET /aegis/history/{id}` returns full snapshots
- `DELETE /aegis/history?older_than_days=90` for TTL pruning

### F8 — Prometheus Metrics (Sprint 1 subset)
- `aegis_scrape_duration_seconds` — histogram
- `aegis_signals_total{severity, metric}` — counter
- `aegis_decisions_total{rule, decision}` — counter
- `aegis_remediations_total{action, status}` — counter
- `aegis_remediation_duration_seconds{action}` — histogram
- `aegis_dlq_depth` — gauge
- `aegis_active_remediations` — gauge

## Test results
- 23 new aegis unit tests: all passing
- 16 original unit tests: all passing
- E2E tests require Docker environment (not run locally)

## Configurable thresholds (env vars)
| Variable | Default | Description |
|---|---|---|
| `AEGIS_SCRAPE_INTERVAL` | 15 | Scrape loop interval in seconds |
| `AEGIS_ACTION_TIMEOUT` | 30 | Action execution timeout |
| `AEGIS_RETRY_MAX` | 3 | Max retries per action |
| `AEGIS_ACTIVE_DEVICES_THRESHOLD` | 2.0 | Min active devices before pressure signal |
| `AEGIS_OTA_IN_PROGRESS_THRESHOLD` | 3.0 | Max OTA deployments before pressure |
| `AEGIS_LATENCY_THRESHOLD` | 0.5 | API latency threshold in seconds |
| `AEGIS_OFFLINE_RATIO_THRESHOLD` | 0.3 | Offline ratio threshold |
