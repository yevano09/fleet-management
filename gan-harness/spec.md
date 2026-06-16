# Product Name: **Aegis — Auto-Remediation Engine for IoT Fleets**

---

## Vision

Aegis is an autonomous remediation layer that sits between Prometheus alerts and fleet infrastructure, converting raw metric signals into decisive, tracked, and learned actions. It transforms the fleet dashboard from a passive observability tool into an active self-healing command center — reducing mean-time-to-resolution (MTTR) from hours to seconds for common resource pressure patterns. By closing the loop between alert fire and remediation execution, Aegis enables a single operator to manage fleets of 10,000+ devices without proportional overhead.

---

## Design Direction

### Brand Identity
- **Codename**: Aegis (Greek: αἰγίς — shield of Zeus, symbolizing protection)
- **Tagline**: "Alerts that act. Fleets that heal."

### Color Palette
| Token | Hex | Usage |
|---|---|---|
| `--aegis-surface` | `#0F1225` | Deep background (darker than existing `#0A0D1A` for layering) |
| `--aegis-raised` | `#181D38` | Card/panel surfaces |
| `--aegis-border` | `#222B4F` | Borders |
| `--aegis-cyan` | `#22D3EE` | Primary accent — decision flow, active remediations |
| `--aegis-cyan-glow` | `rgba(34,211,238,0.08)` | Hover/active glows |
| `--aegis-amber` | `#FBBF24` | Warning-level auto-remediation |
| `--aegis-rose` | `#FB7185` | Escalated/critical — human attention required |
| `--aegis-emerald` | `#34D399` | Successful remediation, green path |
| `--aegis-violet` | `#A78BFA` | Machine-learning predictions, confidence indicators |
| `--aegis-text` | `#E2E8F0` | Primary text |
| `--aegis-muted` | `#64748B` | Secondary/meta text |

### Typography
- **Headings**: `'Sora'` (same as existing dashboard — visual continuity)
- **Body/mono**: `'JetBrains Mono'` for all remediation logs, action outputs, decision traces — remediation is code-like, precise, and should feel technical
- **Scale**: 0.65rem (logs) / 0.8rem (body) / 1.15rem (cards) / 1.5rem (section titles)

### Layout Philosophy
- **Three-column decision pipeline**: INCOMING ALERTS (left) → DECISION ENGINE (center) → REMEDIATION HISTORY (right)
- **Timeline view**: Every remediation action renders as a horizontal timeline entry with status, duration, and outcome
- **No modal overload**: Inline expandable panels instead of modals for action details
- **Dark-first**: Keep existing dark theme; Aegis cards use a subtly deeper surface to distinguish from system cards
- **Motion**: Subtle pulse animation on active remediations; green sweep on success; red flash on failure

### Anti-AI-Slop Directives
- **Zero greeting text**. No "Welcome to Aegis!" or "Let me help you..." — the interface is a tool, not a concierge.
- **No happy-path-only examples**. Every demo must include at least one failed remediation and one escalation path.
- **Real metrics or bust**. All visualizations use real Prometheus metric keys from the existing `fleet_*` namespace. No placeholder chart labels like "Metric 1", "Metric 2".
- **Every action has a trace**. No silent auto-remediation. Every executed action MUST produce a database record, a log line, and a UI entry.
- **No magic thresholds**. All CPU/memory/disk percentage thresholds are configurable via environment variables or settings API. Hard-coded magic numbers are a spec violation.

---

## Features (Prioritized)

---

### MUST-HAVE (Sprint 1–2)

#### F1. Alert Ingestion and Classification Gateway

**Description**: A persistent Prometheus scrape loop (every 15s) that fetches `/metrics/` from the backend, parses all `fleet_*` metrics, classifies incoming readings into resource utilization alerts (CPU, memory, disk, network), and normalizes them into a unified `RemediationSignal` schema. Supports both push (webhook from Alertmanager) and pull (polling) modes.

**Acceptance Criteria**:
- [ ] Polls `GET /metrics/` every 15 seconds (configurable `AEGIS_SCRAPE_INTERVAL`)
- [ ] Parses at minimum: `fleet_active_devices` (gauge → CPU util proxy), `fleet_ota_in_progress` (gauge → disk/network pressure), `fleet_api_request_latency_seconds` (histogram → latency pressure)
- [ ] Normalizes to `RemediationSignal`: `{id, metric_name, value, threshold, severity, timestamp, device_ids, window_seconds, metadata}`
- [ ] Webhook receiver `POST /aegis/ingest` for external Alertmanager integration
- [ ] Emits `aegis_signals_total` Prometheus counter with `severity` and `metric` labels

---

#### F2. Remediation Decision Engine

**Description**: A deterministic + heuristic decision engine that maps classified alerts to remediation actions via a pluggable rule registry. Each rule has a condition function (evaluate signal → bool), an action function (execute remediation), a cooldown, and a max-retry limit. Rules are evaluated in priority order; the first match wins. Supports rule chaining (if action A fails → try action B).

**Acceptance Criteria**:
- [ ] Rule registry with at least 6 built-in rules (see F4 for action list)
- [ ] Each rule defines: `name`, `condition(signal) → bool`, `action(signal) → result`, `cooldown_seconds`, `max_retries`, `priority`
- [ ] Rule chain evaluation: higher priority runs first; if its condition matches, stop
- [ ] Escalation path: if all rules fail → route to `human_escalation` action (creates a critical Alert + Slack ping)
- [ ] Prometheus metric `aegis_decisions_total{rule, decision, duration_ms}`
- [ ] Config-driven rule enable/disable toggle per rule

---

#### F3. Remediation Action Executor

**Description**: An idempotent action execution layer with timeout, retry, rollback, and dead-letter queue. Each action type implements a base `RemediationAction` interface with `execute()`, `rollback()`, `status()` methods. Actions are async, timeout-guarded (default 30s), and logged with full input/output traces.

**Acceptance Criteria**:
- [ ] Base class `RemediationAction` with `execute(signal, context) → RemediationResult` and `rollback(signal, context)`
- [ ] At least 6 concrete action implementations (see F4)
- [ ] Action timeout enforcement (configurable per action type)
- [ ] Retry with exponential backoff (max 3 retries, configurable)
- [ ] Dead-letter queue: actions that exhaust retries go to `DLQ` table for manual review
- [ ] All action results persisted to DB (see F7)

---

#### F4. Built-in Remediation Actions

**Description**: The initial action library covering the most common resource pressure scenarios in an IoT fleet management context.

**Actions**:
| ID | Action | Trigger | Behavior |
|---|---|---|---|
| `R001` | `throttle_ota` | `fleet_ota_in_progress` > 3 + API latency > 500ms | Pause all pending OTA deployments; set OTA throttle flag; resume after 5 min or manual override |
| `R002` | `mqtt_qos_downgrade` | MQTT message volume spike | Downgrade non-critical topics from QoS 1 to QoS 0; log which topics affected |
| `R003` | `device_soft_restart` | Device signal < -90 + uptime > 24h | Publish MQTT `command/restart` to affected devices; monitor heartbeat for reconnection |
| `R004` | `scale_heartbeat` | Offline > 30% | Increase heartbeat frequency from 10s to 5s for remaining online devices to detect further drop |
| `R005` | `rollback_ota_batch` | OTA failure spike > 30% | Identify last batch; roll back to `previous_firmware_version` for affected devices; create alert |
| `R006` | `human_escalation` | All auto-remediation exhausted | Create critical Alert; send Slack + Email with full trace; auto-assign to on-call rotation |
| `R007` | `migrate_device_pool` | Single device CPU/memory > 90% for 5min | If device is in a load-balanced group, temporarily route traffic away; mark device for inspection |
| `R008` | `cleanup_firmware_artifacts` | Disk pressure (firmware directory > 80% of allowed) | Delete oldest resolved OTA artifacts; log freed space |

**Acceptance Criteria**:
- [ ] All 8 actions implemented with full `execute()`, `rollback()`, `status()` 
- [ ] Each action has unit tests with at least one success + one failure path
- [ ] Actions are idempotent: running twice with same signal is safe

---

#### F5. Remediation History and Audit Trail

**Description**: A new DB model `Remediation` that records every signal→decision→action→outcome as an immutable audit entry. Supports full-text search, filtering by status/action/device, and export to JSON. Entries are never deleted — only soft-expired via TTL.

**Acceptance Criteria**:
- [ ] `Remediation` model with fields: `id`, `signal_id`, `metric_name`, `value`, `threshold`, `severity`, `rule_name`, `action_name`, `status` (pending/in_progress/success/failed/rolled_back/escalated), `input_snapshot` (JSON), `output_snapshot` (JSON), `error_message`, `started_at`, `completed_at`, `duration_ms`, `retry_count`, `device_ids`
- [ ] `GET /aegis/history` with pagination + filter (status, action, metric, time range)
- [ ] `GET /aegis/history/{id}` returns full input/output snapshots
- [ ] `DELETE /aegis/history?older_than_days=90` for TTL-based pruning
- [ ] Prometheus `aegis_remediations_total{action, status}` counter

---

#### F6. Dashboard Remediation Panel

**Description**: A new section on the Fleet Commander dashboard that renders the Aegis decision pipeline as three live panels: incoming signals, active remediations, and history timeline. Updates auto-refresh every 10s.

**Acceptance Criteria**:
- [ ] Panel renders in the existing `dashboard.html` between alerts and device table
- [ ] Left column: last 10 signals with severity badges, sparkline of metric trend (last 5 values)
- [ ] Center column: active (in_progress) remediations with animated pulse indicator, elapsed time, cancel button
- [ ] Right column: last 20 remediation history entries as a compact timeline; green/amber/red dots per outcome
- [ ] Clicking any entry expands inline with full input/output snapshots and a "Re-run" button
- [ ] Top of panel: summary bar "X auto-resolved / Y escalated / Z pending" with sparkline count

---

#### F7. REST + CLI Agent Integration

**Description**: Aegis follows the existing Fleet Commander dual-mode agent pattern — async DB tools for in-backend, HTTP tools for CLI. Adds a new REST router `/aegis/` and new CLI flags `--aegis` and `--aegis-action` in `run_agents.py`.

**Acceptance Criteria**:
- [ ] `agents/async_tools.py` gains async functions: `async_detect_resource_pressure(db)`, `async_run_remediation_cycle(db)`, `async_get_remediation_history(db, ...)`
- [ ] `agents/tools.py` gains HTTP wrappers: `detect_resource_pressure()`, `run_remediation_cycle()`, `get_remediation_history()`, `rerun_remediation(id)`
- [ ] `agents/phase1_crew.py` gains `run_remediation_agent()` function following the same agent dict pattern
- [ ] `agents/routers.py` gains `GET /aegis/scan`, `GET /aegis/history`, `POST /aegis/rerun/{id}`
- [ ] `run_agents.py` gains `--remediate`, `--remediation-history`, `--remediation-rerun <id>` flags
- [ ] Agent panel in dashboard renders `remediation` type with signal details and outcome timeline

---

#### F8. Prometheus Metrics for Auto-Remediation

**Description**: Full observability of the Aegis system itself via Prometheus metrics. Every component — ingestion, decision engine, action executor, DLQ — emits metrics for monitoring the monitor.

**Acceptance Criteria**:
- [ ] `aegis_scrape_duration_seconds` — histogram of metric poll duration
- [ ] `aegis_signals_total{severity, metric}` — counter of signals classified
- [ ] `aegis_decisions_total{rule, decision}` — counter of rule match/no-match
- [ ] `aegis_remediations_total{action, status}` — counter of remediation outcomes
- [ ] `aegis_remediation_duration_seconds{action}` — histogram of action execution time
- [ ] `aegis_dlq_depth` — gauge of dead-letter queue entries
- [ ] `aegis_active_remediations` — gauge of currently in-progress actions
- [ ] All metrics documented in `METRICS.md`

---

### SHOULD-HAVE (Sprint 3–4)

#### F9. Learning from Outcomes (Feedback Loop)

**Description**: A lightweight Bayesian weight adjuster that tracks which rules succeed/fail for which metric patterns and adjusts rule priority weights accordingly. No external ML infra — pure SQLite-backed counters.

**Acceptance Criteria**:
- [ ] `RuleWeight` model: `rule_name`, `metric_pattern` (glob), `success_count`, `failure_count`, `last_weight`, `updated_at`
- [ ] After each remediation cycle, increment success/failure counter
- [ ] Weight = `success_count / max(success_count + failure_count, 1)` — recalculated every 10 cycles
- [ ] Rule evaluation order uses weight as tiebreaker (higher weight = higher priority within same priority tier)
- [ ] `POST /aegis/weights/reset` to reset all weights
- [ ] Dashboard renders weight bars next to rule names in settings view

---

#### F10. Remediation Dry-Run Mode

**Description**: A safety mode where the decision engine runs fully, logs what it *would* do, but does not execute actions. Output is identical to real mode except `status: "dry_run"` and no side effects.

**Acceptance Criteria**:
- [ ] Global toggle `AEGIS_DRY_RUN=true` in env
- [ ] Dry-run creates `Remediation` records with `status = "dry_run"` and full input/output snapshots
- [ ] Dashboard dry-run indicator banner (amber strip across top)
- [ ] Dry-run history filterable separately
- [ ] CLI flag `--remediate-dry-run` for testing without side effects

---

#### F11. Rule Configuration API

**Description**: REST API for live rule management — enable/disable, update thresholds, adjust cooldowns, reorder priority — without restarting the backend.

**Acceptance Criteria**:
- [ ] `GET /aegis/rules` — list all rules with config
- [ ] `PUT /aegis/rules/{rule_name}` — update threshold, cooldown, max_retries, enabled
- [ ] `PUT /aegis/rules/reorder` — change priority order
- [ ] Changes persisted to DB `RuleConfig` model
- [ ] In-memory cache refreshed on update

---

#### F12. Notification for Remediation Outcomes

**Description**: Extends existing `AlertEngine` notification channels to fire on remediation outcomes — success, failure, escalation, rollback. Separate Slack channel per outcome type (e.g. `#aegis-success`, `#aegis-failures`).

**Acceptance Criteria**:
- [ ] `AEGIS_SLACK_SUCCESS_WEBHOOK`, `AEGIS_SLACK_FAILURE_WEBHOOK`, `AEGIS_SLACK_ESCALATION_WEBHOOK` env vars
- [ ] Slack messages include: rule name, action, metric, device IDs, duration, outcome
- [ ] Email notification option via existing SMTP config
- [ ] Rate-limited to max 1 notification per rule per 5 minutes

---

### NICE-TO-HAVE (Sprint 5+)

#### F13. Correlation Engine — Multi-Metric Anomaly Signatures

**Description**: Pattern matcher that correlates signals across metrics (e.g. high CPU + high latency + OTA in progress = "OTA storm") and triggers composite remediation. Instead of firing 3 separate actions, fires 1 composite action.

**Acceptance Criteria**:
- [ ] Signature registry with pattern definitions (AND/OR of metric thresholds within a time window)
- [ ] Built-in signature: "OTA Storm" = `ota_in_progress > 2 and latency_p99 > 1000ms` → runs throttle_ota
- [ ] Built-in signature: "Device Rot" = `signal < -90 and uptime > 48h and soh < 80%` → runs migrate_device_pool
- [ ] Signature match produces a single composite `Remediation` record with sub-action array

---

#### F14. Remediation Scheduler — Recurring Health Checks

**Description**: CRON-style scheduler for periodic remediation scans with different thresholds (e.g. aggressive during business hours, relaxed overnight). Schedule definitions stored in DB.

**Acceptance Criteria**:
- [ ] Schedule model: `name`, `cron_expression`, `rule_overrides` (JSON of rule→modified thresholds), `enabled`
- [ ] Scheduler evaluates next tick every 60s via asyncio loop
- [ ] Schedule override thresholds (e.g. "Night mode: SLA 2x")
- [ ] Dashboard schedule management UI

---

#### F15. On-Call Rotation Integration

**Description**: PagerDuty/Opsgenie-style on-call calendar for escalation routing. When `human_escalation` fires, routes to current on-call operator based on schedule.

**Acceptance Criteria**:
- [ ] `OnCallEntry` model: `user`, `start_time`, `end_time`, `channel` (slack/email/sms)
- [ ] `GET /aegis/on-call/current` returns active on-call operator
- [ ] Escalation routes to operator's preferred channel
- [ ] Override support for temporary swaps

---

#### F16. Time-Series Visualization of Remediation Impact

**Description**: Grafana dashboard for Aegis metrics + before/after metric comparison for each action type. Shows whether remediation actually improved the metric.

**Acceptance Criteria**:
- [ ] Grafana dashboard JSON (provisioned via `grafana/dashboards/aegis.json`)
- [ ] Panels: signal rate, decision distribution, action success rate, MTTR over time
- [ ] Before/after overlay: for each action type, overlays metric value 2min before and 5min after remediation
- [ ] Dashboard auto-provisioned on Grafana startup

---

## Technical Stack

### Existing Stack (inherited)
| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| Database | SQLAlchemy async + SQLite (→ PostgreSQL in prod) |
| Metrics | Prometheus Client (Python) |
| MQTT | Paho MQTT Client v5 |
| Notifications | Slack webhook, SMTP, generic webhook |
| Agents | Crew AI (optional), custom heuristic agents |
| Dashboard | Jinja2 + HTMX + vanilla JS |
| Container | Docker Compose |
| Charts | Prometheus + Grafana |

### New Additions
| Component | Technology | Rationale |
|---|---|---|
| Remediation Engine | Pure Python (no new deps) | Must run inside existing FastAPI process |
| Rule Registry | Python dict + `app/aegis/rules/` module | Pluggable, testable, no framework overhead |
| Background Scheduler | `asyncio.create_task` + loop | No Celery/Redis dependency; keep it light |
| DLQ | SQLite table (existing DB) | No external queue needed at this scale |
| Outcome Learning | SQLite counter tables | Bayesian weight adjuster, no ML infra |
| Time-Series Visuals | Grafana (existing) | Provisioned dashboard JSON |
| Configuration | Existing `.env` + `Settings` class | Extends `app/config.py` |

### New Files
```
app/aegis/
  __init__.py
  engine.py             # Main remediation cycle orchestrator
  rules.py              # Rule registry + built-in rules
  actions.py            # RemediationAction base + implementations
  models.py             # Remediation + RuleWeight + Schedule + OnCall models
  schemas.py            # Pydantic schemas for API
  scheduler.py          # Background scrape + decision loop
  router.py             # REST endpoints
  metrics.py            # Aegis-specific Prometheus metrics
  config.py             # Aegis-specific Settings extension

agents/
  async_tools.py        # +remediation functions
  tools.py              # +remediation HTTP wrappers
  phase1_crew.py        # +remediation agent
  routers.py            # +/aegis/ endpoints

app/templates/
  dashboard.html        # +remediation panel
```

### Dependency Changes
- **No new Python dependencies**. Everything uses stdlib + existing packages (FastAPI, SQLAlchemy, prometheus_client, requests).

---

## Evaluation Criteria

### Design Quality (0.3)
| Criterion | 0-3 | 4-6 | 7-10 |
|---|---|---|---|
| Visual hierarchy | Panels are flat, no information layering | Basic layering exists but inconsistent | Three-column pipeline is instantly readable; color + depth encode state |
| Motion & feedback | No animation on state changes | Basic fade-in | Pulse on active, sweep on success, flash on error; all under 200ms |
| Consistency with existing UI | Clashes with dashboard colors | Similar colors but different component patterns | Same font stack, same border radii, same spacing scale; Aegis sections visually distinct via surface depth |
| Readability of decision traces | JSON blobs in UI | Tables with truncated text | Syntax-highlighted collapsible input/output panels; key-value pairs with color-coded diffs |

### Originality (0.2)
| Criterion | 0-3 | 4-6 | 7-10 |
|---|---|---|---|
| Beyond "alert → email" | Just sends more notifications | Routes alerts through basic if/else | Rule chain, dry-run mode, outcome learning, composite signatures — novel IoT-specific actions |
| IoT-specific actions | Generic restart/scale | Device-aware (signal, firmware) | `mqtt_qos_downgrade`, `cleanup_firmware_artifacts`, `device_soft_restart` — specific to fleet mgmt |
| Learning approach | Not attempted | Hard-coded weights | Bayesian weight adjustment from outcome history; no ML infra dependency |

### Craft (0.3)
| Criterion | 0-3 | 4-6 | 7-10 |
|---|---|---|---|
| Error handling | No try/except on action execution | Basic exception logging | Timeout guards, retry with backoff, dead-letter queue, rollback on each action, partial-failure recovery |
| Testing | No tests | Unit tests for rules | Unit tests for ALL actions (success + failure path), integration test for full cycle, dry-run comparison test, DLQ overflow test |
| Observability | No metrics | Basic success/failure counter | 7 Prometheus metrics, structured logging with `extra={rule, action, signal_id}`, Grafana dashboard |
| Idempotency | Not considered | Documented but not enforced | Double-execution produces same result (or no-op); safe to rerun any action from UI |

### Functionality (0.2)
| Criterion | 0-3 | 4-6 | 7-10 |
|---|---|---|---|
| Alert coverage | Only CPU | CPU + memory + disk | CPU, memory, disk, network, OTA, signal, latency — all existing `fleet_*` metrics covered |
| Action completeness | 2 actions | 4 actions | 8+ actions including composite correlation (Sprint 5) |
| Integration | Standalone process | REST API only | REST API + CLI agent + dashboard panel + MQTT integration + Slack notification per outcome |
| Safety | No safety mechanisms | Dry-run mode only | Dry-run mode + configurable cooldowns + max retry limits + dead-letter queue + human escalation path + rollback per action |

---

## Sprint Plan

### Sprint 1 — Foundation (Days 1–10)
**Goal**: Core engine with ingestion + decision + 4 actions + history DB

**Features**: F1 (Alert Ingestion), F2 (Decision Engine), F3 (Action Executor), F5 (History + Audit)

**Deliverables**:
- `app/aegis/engine.py` with scrape loop and rule registry
- `app/aegis/actions.py` with actions R001–R004
- `app/aegis/models.py` with `Remediation` + `RuleConfig` models
- Migration adds `remediations` and `rule_configs` tables
- `GET /aegis/history` endpoint with pagination
- F8 metrics: `aegis_signals_total`, `aegis_decisions_total`, `aegis_scrape_duration_seconds`
- Unit tests for engine + rules + first 4 actions

### Sprint 2 — Integration (Days 11–20)
**Goal**: Dashboard panel + CLI flags + remaining actions + dry-run mode

**Features**: F4 (actions R005–R008), F6 (Dashboard), F7 (REST + CLI), F10 (Dry-run)

**Deliverables**:
- `dashboard.html` remediation panel with three-column layout
- `run_agents.py` flags `--remediate`, `--remediation-history`
- `agents/routers.py` endpoints `GET /aegis/scan`, `GET /aegis/history`
- Actions R005 (rollback_ota_batch), R006 (human_escalation), R007 (migrate_device_pool), R008 (cleanup_firmware_artifacts)
- Dry-run mode toggle
- Integration tests for full cycle (including dry-run comparison)

### Sprint 3 — Intelligence (Days 21–30)
**Goal**: Learning loop + rule management API + outcome notifications

**Features**: F9 (Learning from Outcomes), F11 (Rule Config API), F12 (Notification)

**Deliverables**:
- `RuleWeight` model + Bayesian weight calculation
- Rule management CRUD API
- Outcome-specific Slack channels (`#aegis-success`, `#aegis-failures`, `#aegis-escalations`)
- All 8 actions have unit tests with failure paths
- F8 metrics complete (all 7 metrics)
- `METRICS.md` documentation

### Sprint 4 — Polish & Scale (Days 31–40)
**Goal**: Composite signatures + scheduler + on-call + Grafana

**Features**: F13 (Correlation Engine), F14 (Scheduler), F15 (On-Call), F16 (Grafana)

**Deliverables**:
- Signature registry with "OTA Storm" and "Device Rot" patterns
- CRON scheduler with schedule management UI
- On-call rotation model + escalation routing
- Provisioned Grafana dashboard `grafana/dashboards/aegis.json`
- Full E2E tests: scrape → classify → decide → execute → log → notify
- Documentation: `AEGIS.md` with architecture overview, config reference, action catalog

### Sprint 5+ — Stretch
- Time-series Grafana dashboards with before/after impact
- Multi-metric correlation signatures (expand beyond 2)
- PagerDuty native integration
- Remediation playbook export (markdown)
- Fleet-wide recommendation engine ("Based on patterns across 1000 devices, consider...")
