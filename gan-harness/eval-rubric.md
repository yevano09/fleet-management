# Aegis — Evaluation Rubric

## Scoring: 1–10 per criterion, weighted total out of 10.

---

## 1. Design Quality (weight: 0.3)

### 1.1 Visual Hierarchy & Layout (0–10)

| Score | Description |
|---|---|
| 1–3 | Single-column list of alerts; no distinction between signal, decision, and outcome |
| 4–5 | Two-panel layout (alert list + history) but no active-remediation tracking |
| 6–7 | Three-column layout exists but columns bleed into each other; inconsistent spacing |
| 8–9 | Three-column pipeline (signals / active remediations / history) with clear visual separation via surface depth (`--aegis-surface` vs `--aegis-raised`); consistent 1rem gaps |
| 10 | Three-column layout + inline expandable traces + animated state indicators + sparkline per signal column; no modal dialogs for inspection; all information accessible at a glance |

### 1.2 Color & Typography (0–10)

| Score | Description |
|---|---|
| 1–3 | Uses default system colors; no remediation-specific palette |
| 4–5 | Some color but inconsistent with existing dashboard |
| 6–7 | Uses the Aegis color palette but missing one or more tokens (e.g. no `--aegis-rose` for escalation) |
| 8–9 | Full palette applied correctly: cyan for decision flow, rose for escalation, emerald for success, amber for warnings; `JetBrains Mono` for all remediation traces |
| 10 | Same font stack + sizing as dashboard; Aegis sections have slightly deeper background (`#0F1225`) for visual nesting; every color token has a semantic meaning visible in the UI |

### 1.3 Motion & Feedback (0–10)

| Score | Description |
|---|---|
| 1–3 | Static UI; no animation on state changes |
| 4–5 | Page-level refresh only |
| 6–7 | Pulse animation on active remediations but duration > 500ms (feels sluggish) |
| 8–9 | Subtle pulse (< 300ms) on active actions; green sweep on success; red flash on failure; no motion on scroll or hover |
| 10 | All state-change animations complete in < 200ms; active remediations have a breathing pulse (opacity 0.6→1.0 over 2s); no motion for motion's sake |

### 1.4 Consistency & Integration (0–10)

| Score | Description |
|---|---|
| 1–3 | Looks like a separate app bolted onto the dashboard |
| 4–5 | Same colors but different border radii, spacing, or font sizes |
| 6–7 | Matches dashboard visual tokens but adds new interaction patterns (e.g. modals where dashboard uses inline) |
| 8–9 | Same design tokens (`--bg-*`, `--text-*`, `--border`, `--font-*`); same component patterns (badges, stat-cards, tables); Aegis distinct only via surface depth |
| 10 | Full token parity with dashboard; Aegis panel could be mistaken for a natively planned dashboard section; follows HTMX patterns where existing UI uses HTMX |

---

## 2. Originality (weight: 0.2)

### 2.1 Beyond Passive Alerts (0–10)

| Score | Description |
|---|---|
| 1–3 | Routes alerts to email/Slack only — same as existing alert engine |
| 4–5 | Executes simple actions (restart device) but no decision priority or chaining |
| 6–7 | Rule chain evaluation with priority but no learning or correlation |
| 8–9 | Rule chain + priority + dry-run mode + outcome tracking + Bayesian weight adjustment |
| 10 | Rule chain + priority + dry-run + outcome learning + multi-metric correlation signatures + CRON schedules + on-call routing closed into a single coherent system |

### 2.2 IoT-Specific Remediation (0–10)

| Score | Description |
|---|---|
| 1–3 | Generic actions: scale up, restart server, send email |
| 4–5 | Device-aware but only status (online/offline) |
| 6–7 | Device-aware: signal strength, firmware version, OTA status, battery SoH |
| 8–9 | Actions are specific to IoT fleet management: `throttle_ota`, `mqtt_qos_downgrade`, `device_soft_restart`, `rollback_ota_batch`, `cleanup_firmware_artifacts` |
| 10 | All 8 actions are uniquely suited to IoT fleets; no generic cloud-infra actions; each action can be traced to a real IoT fleet problem pattern |

### 2.3 Learning Without ML Infra (0–10)

| Score | Description |
|---|---|
| 1–3 | No learning; all rules are static |
| 4–5 | Manual weight adjustment via config file |
| 6–7 | Counter-based success/failure tracking visible in UI |
| 8–9 | Bayesian weight adjustment: `weight = success / max(total, 1)` recalculated every 10 cycles; weights affect rule priority |
| 10 | Bayesian weights + per-signature-pattern success rates + UI showing weight evolution over time + weight reset API; all without a single ML dependency |

---

## 3. Craft (weight: 0.3)

### 3.1 Error Handling & Resilience (0–10)

| Score | Description |
|---|---|
| 1–3 | No try/except on action execution; one failure crashes the cycle |
| 4–5 | Basic exception catching but no retry or rollback |
| 6–7 | Retry with fixed delay but no backoff; partial rollback on some actions |
| 8–9 | Exponential backoff retry (max 3), timeout guards (30s per action, configurable), rollback on every action, dead-letter queue for exhausted retries |
| 10 | All of previous + partial-failure recovery (if 5/10 devices fail, roll back the 5 and keep the 5 successful) + DLQ dashboard view with "Re-run" button + human escalation as safety net |

### 3.2 Testing Coverage (0–10)

| Score | Description |
|---|---|
| 1–3 | No tests |
| 4–5 | Unit tests for 2–3 rules only |
| 6–7 | Unit tests for all 8 actions (success path only) |
| 8–9 | Unit tests for ALL actions (success + failure path) + integration test for full cycle (scrape → classify → decide → execute → log) |
| 10 | All of previous + dry-run comparison test (same signal → same decision, no side effects) + DLQ overflow test + concurrent signal test + rule chain priority test + idempotency test (run same action twice → same result) |

### 3.3 Observability (0–10)

| Score | Description |
|---|---|
| 1–3 | No metrics beyond existing dashboard |
| 4–5 | One counter (`aegis_remediations_total`) |
| 6–7 | 3–4 metrics: total remediations, active gauge, duration histogram |
| 8–9 | All 7 Aegis metrics implemented: scrape duration, signal count, decision count, remediation count, remediation duration, DLQ depth, active gauge |
| 10 | All 7 metrics + structured logging (`extra={rule, action, signal_id}`) + Grafana dashboard with 5 panels + `METRICS.md` documenting every metric |

### 3.4 Idempotency & Safety (0–10)

| Score | Description |
|---|---|
| 1–3 | No idempotency considered; running action twice causes issues |
| 4–5 | Documented "should be safe" but no test |
| 6–7 | Each action tracks its own in-progress state to prevent double execution |
| 8–9 | Actions are provably idempotent: `execute(signal) + execute(signal) = execute(signal)`; rollback restores pre-action state; dry-run mode produces identical decision output without side effects |
| 10 | All of previous + idempotency test for every action + max-retry circuit breaker + cooldown enforcement per rule + human escalation if all retries exhausted |

---

## 4. Functionality (weight: 0.2)

### 4.1 Alert Coverage Breadth (0–10)

| Score | Description |
|---|---|
| 1–3 | Monitors 1 metric (e.g. CPU) |
| 4–5 | Monitors 2–3 metrics |
| 6–7 | Monitors 4–5 metrics: CPU, memory, disk, network, OTA |
| 8–9 | Monitors all existing `fleet_*` metrics: active devices, OTA in-progress, API latency, signal strength, SOH, offline count |
| 10 | All existing metrics + composite signals (correlated multi-metric patterns) + webhook ingestion from external Prometheus Alertmanager |

### 4.2 Action Depth & Variety (0–10)

| Score | Description |
|---|---|
| 1–3 | 2 actions implemented |
| 4–5 | 4 actions implemented |
| 6–7 | 6 actions implemented |
| 8–9 | All 8 actions implemented with execute() + rollback() + status() |
| 10 | 8 actions + multi-metric correlation (composite actions) + scheduled actions + dry-run on all |

### 4.3 Integration Surface (0–10)

| Score | Description |
|---|---|
| 1–3 | Standalone script |
| 4–5 | REST API only |
| 6–7 | REST API + dashboard panel |
| 8–9 | REST API + dashboard panel + CLI flags (`--remediate`, `--remediation-history`, `--remediation-rerun`) + agent panel in dashboard |
| 10 | REST API + dashboard + CLI + agent panel + MQTT integration (publish remediation commands) + Slack notification per outcome type + Grafana dashboard |

### 4.4 Safety & Governance (0–10)

| Score | Description |
|---|---|
| 1–3 | No safety mechanisms; actions fire immediately |
| 4–5 | Dry-run mode exists but not toggleable at runtime |
| 6–7 | Dry-run toggle + configurable cooldowns per rule |
| 8–9 | Dry-run toggle + cooldowns + max retries + dead-letter queue + human escalation path + rollback per action |
| 10 | All of previous + per-rule enable/disable toggle + schedule-based threshold overrides + on-call escalation routing + audit trail (immutable history, no deletions) |

---

## Scoring Template

| Criterion | Weight | Score (1–10) | Weighted |
|---|---|---|---|
| 1. Design Quality | 0.3 | | |
| 1.1 Visual Hierarchy | (0.25 of design) | | |
| 1.2 Color & Typography | (0.25 of design) | | |
| 1.3 Motion & Feedback | (0.20 of design) | | |
| 1.4 Consistency | (0.30 of design) | | |
| **Design subtotal** | **0.3** | **(avg of 1.1–1.4)** | **× 0.3** |
| 2. Originality | 0.2 | | |
| 2.1 Beyond Passive Alerts | (0.35 of orig) | | |
| 2.2 IoT-Specific Actions | (0.40 of orig) | | |
| 2.3 No-ML Learning | (0.25 of orig) | | |
| **Originality subtotal** | **0.2** | **(avg of 2.1–2.3)** | **× 0.2** |
| 3. Craft | 0.3 | | |
| 3.1 Error Handling | (0.30 of craft) | | |
| 3.2 Testing | (0.25 of craft) | | |
| 3.3 Observability | (0.25 of craft) | | |
| 3.4 Idempotency | (0.20 of craft) | | |
| **Craft subtotal** | **0.3** | **(avg of 3.1–3.4)** | **× 0.3** |
| 4. Functionality | 0.2 | | |
| 4.1 Alert Coverage | (0.25 of func) | | |
| 4.2 Action Depth | (0.30 of func) | | |
| 4.3 Integration | (0.25 of func) | | |
| 4.4 Safety | (0.20 of func) | | |
| **Functionality subtotal** | **0.2** | **(avg of 4.1–4.4)** | **× 0.2** |
| **TOTAL** | **1.0** | | **∑ weighted** |

### Grade Bands

| Total Score | Grade | Meaning |
|---|---|---|
| 9.0–10.0 | S | Production-ready. Ship it. |
| 7.5–8.9 | A | Strong. Minor polish needed. |
| 6.0–7.4 | B | Good. Some gaps in safety or coverage. |
| 4.0–5.9 | C | Below bar. Needs significant work. |
| 0–3.9 | D | Restart. Misunderstood the problem. |

### Must-Pass Gates (score < 6 → automatic C)

- **3.1 (Error Handling)**: Must have retry + rollback on at least 4 actions.
- **3.2 (Testing)**: Must have unit tests for at least 6 actions.
- **4.1 (Alert Coverage)**: Must monitor at least 3 distinct `fleet_*` metrics.
- **4.4 (Safety)**: Must have dry-run mode AND human escalation path.
