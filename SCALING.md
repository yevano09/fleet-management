# Scaling Strategy — V2G Arbitrage with Battery Degradation Pricing

This document outlines the medium- and long-term scaling roadmap for the V2G
arbitrage module.

---

## 1. ML Models for Spot Price Forecasting

### Current
- `mock_spot_prices()` generates synthetic diurnal prices for demo/testing.

### Medium-term
- Replace with a **LightGBM / XGBoost regressor** trained on historical
  day-ahead market prices (e.g., PJM, CAISO, Nord Pool).
- Features: hour-of-day, day-of-week, holiday flag, lag-24h price, solar/wind
  generation forecast, temperature forecast.
- Expose via `/agents/price-forecast` endpoint returning 24–48 hourly prices
  with confidence intervals.

### Long-term
- **LSTM / Transformer** sequence model capturing long-range temporal
  dependencies and price spikes.
- Ensemble: combine point forecast from LightGBM with probabilistic forecast
  from a deep model.
- Model retraining pipeline: daily retrain on last 90 days of data, triggered
  by Airflow or Prefect.

---

## 2. Protocol Support — OCPP & OpenADR

### OCPP 2.0.1 (EVSE Integration)
- Replace custom MQTT V2G topics with **OCPP 2.0.1** messages over WebSocket.
- Key OCPP messages for V2G:
  - `TransactionEvent` — start/stop charging session
  - `SetChargingProfile` — schedule charge/discharge power limits
  - `GetCompositeSchedule` — query available capacity
  - `NotifyEvent` — real-time EVSE status
- Library: [ocpp](https://github.com/mobilityhouse/ocpp) (Python).

### OpenADR 2.0b (DERMS Integration)
- Implement Virtual End Node (VEN) role to receive Demand Response events.
- Map openADR `EiEvent` signals to V2G discharge commands:
  - `moderate` → discharge at 50% power
  - `high` → discharge at 100% power
  - `critical` → discharge at 100% + restrict charging
- Respond with `CreatedEvent` and `OptOut` for device-level constraints.

### Architecture
```
                   +-----------------+
  EVSE (OCPP) <--> | Fleet Commander | <--> DERMS (OpenADR)
                   +-----------------+
                           |
                       [MQTT bridge]
                           |
                    IoT Devices/EVs
```

---

## 3. TimescaleDB for Time-Series Data

### Current
- SQLite with SQLAlchemy ORM. Battery data (SOC, SOH, temp) stored in the
  `devices` table as scalar columns.

### Medium-term
- Migrate to **TimescaleDB** (PostgreSQL extension) for high-ingestion
  time-series data.
- Create hypertables:
  ```sql
  CREATE TABLE battery_telemetry (
    time        TIMESTAMPTZ NOT NULL,
    device_id   TEXT NOT NULL,
    soc         DOUBLE PRECISION,
    soh         DOUBLE PRECISION,
    battery_temp DOUBLE PRECISION,
    plug_status TEXT
  );
  SELECT create_hypertable('battery_telemetry', by_range('time'));
  ```
- Keep device metadata (name, firmware, config) in regular PostgreSQL tables.
- Use **continuous aggregates** for downsampling: hourly avg SOC, daily SOH trend.

### Long-term
- **Columnar compression** on hypertables > 6 months old (storage savings ~90%).
- **Data retention policies**: raw telemetry 90 days, hourly aggregates 2 years.
- Query pattern: `/agents/v2g-dispatch?device_id=X` reads the latest
  `battery_telemetry` row plus 24h of forecast data from a materialised view.

---

## 4. MILP Optimisation with Stochastic Scenarios

### Current
- Heuristic greedy algorithm: simple, fast, deterministic.

### Medium-term
- Replace with **PuLP** or **OR-Tools** MILP solver.
- Decision variables: `E_ch[t]`, `E_dis[t]` (continuous, 0..max_power).
- Objective:
  ```
  max sum_t (P[t]*E_dis[t] - P[t]*E_ch[t] - C_deg[t]*E_dis[t])
  ```
- Constraints:
  - `SOC[t+1] = SOC[t] + η_ch*E_ch[t] - E_dis[t]/η_dis`
  - `SOC_min ≤ SOC[t] ≤ SOC_max`
  - `SOC[T_dep] ≥ SOC_req`
  - At most one of `E_ch[t]`, `E_dis[t]` > 0 (big-M constraint).

### Long-term
- **Stochastic MILP**: run `N` price scenarios (from ML forecast quantiles).
- Objective becomes expected value across scenarios, with CVaR term for
  risk-aversion.
- **Rolling horizon**: re-solve every hour with updated SOC and prices.
- Deploy as a separate microservice (`v2g-solver`) scaled horizontally,
  communicating via Redis pub/sub.

---

## 5. Observability & Alerting

- Prometheus metrics already added:
  - `fleet_v2g_active_discharges`
  - `fleet_v2g_projected_revenue_dollars`
  - `fleet_battery_degradation_cost_dollars`
  - `fleet_device_soc{device="..."}`
- Add **Alertmanager rules**:
  ```yaml
  - alert: BatteryDegradationSpike
    expr: rate(fleet_battery_degradation_cost_dollars[1h]) > 10
    for: 5m
    labels: { severity: warning }
  - alert: V2GRevenueLoss
    expr: fleet_v2g_projected_revenue_dollars < 0
    for: 10m
    labels: { severity: critical }
  ```
- Grafana dashboard already includes panels for all V2G metrics.

---

## 6. Deployment & Testing

- **Unit tests**: `tests/test_v2g.py` covers degradation cost, heuristic
  optimiser decisions, SOC constraints.
- **Integration tests**: extend `tests/test_e2e.py` to call
  `/agents/v2g-dispatch` and verify Prometheus metrics are exposed.
- **Docker Compose profile**: V2G features work with existing `--profile demo`
  profile; no new services required.
- **CI/CD**: GitHub Actions workflow runs `pytest tests/test_v2g.py` on push.
