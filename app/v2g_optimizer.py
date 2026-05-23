"""
Battery degradation model and V2G arbitrage heuristic optimizer.

C_deg(SOH, temp)  – degradation cost per kWh cycled
heuristic_optimize – produces charge/discharge schedule that maximizes
                     sum(P_grid * E_dis - P_grid * E_ch - C_deg * |E_dis|)
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Default replacement-cost curve (USD)
REPLACEMENT_COST = settings.battery_replacement_cost_dollars  # 35 000
CAPACITY_KWH = settings.battery_capacity_kwh  # 60
SOH_MIN_DISCHARGE = settings.soh_min_discharge  # 0.7
SOH_DEG_THRESHOLD = settings.soh_deg_threshold  # 0.8
HORIZON_HOURS = settings.v2g_horizon_hours  # 24
STEP_MINUTES = settings.v2g_time_step_minutes  # 60


@dataclass
class DegradationParams:
    soh: float
    battery_temp: float
    replacement_cost: float = REPLACEMENT_COST
    capacity_kwh: float = CAPACITY_KWH
    soh_min_discharge: float = SOH_MIN_DISCHARGE
    soh_deg_threshold: float = SOH_DEG_THRESHOLD


def degradation_cost_per_kwh(params: DegradationParams) -> float:
    """Return C_deg (USD per kWh cycled) for a single discharge event.

    Rules:
    - No discharge allowed when SOH < soh_min_discharge (returns inf).
    - Cost is minimal when SOH >= soh_deg_threshold.
    - Cost rises exponentially as SOH drops below the threshold.
    - Higher battery temperature accelerates degradation (Arrhenius factor).
    """
    if params.soh < params.soh_min_discharge:
        return float("inf")

    # Base degradation: fraction of battery value lost per full cycle
    # A typical EV battery lasts ~1000 full cycles before reaching 70% SOH.
    # That means 0.03 % capacity fade per full cycle on average.
    base_fade_per_cycle = 0.0003  # 0.03 % per full cycle

    # SOH multiplier: cost rises when SOH < threshold
    if params.soh >= params.soh_deg_threshold:
        soh_factor = 1.0
    else:
        soh_factor = (params.soh_deg_threshold / max(params.soh, 0.01)) ** 2

    # Temperature Arrhenius factor: T_ref = 25 C, doubles every 10 C
    temp_factor = 2.0 ** ((params.battery_temp - 25.0) / 10.0)
    temp_factor = max(temp_factor, 0.5)  # floor at 0.5 (cold operation)

    # Effective capacity fade for cycling 1 kWh
    fade_per_kwh = base_fade_per_cycle / params.capacity_kwh * soh_factor * temp_factor

    # Cost = replacement cost * fraction of battery life consumed
    return params.replacement_cost * fade_per_kwh


def mock_spot_prices(hours: int = HORIZON_HOURS, base_price: float = 0.10) -> list[float]:
    """Generate synthetic day-ahead spot prices (USD/kWh) for testing."""
    now = datetime.now(timezone.utc)
    prices = []
    for h in range(hours):
        hour_of_day = (now.hour + h) % 24
        # Simple diurnal pattern: cheap at night, expensive in evening peak
        if 2 <= hour_of_day < 6:
            p = base_price * random.uniform(0.3, 0.6)
        elif 17 <= hour_of_day < 21:
            p = base_price * random.uniform(2.0, 4.0)
        else:
            p = base_price * random.uniform(0.8, 1.5)
        prices.append(round(p, 4))
    return prices


@dataclass
class SlotResult:
    start_time: str
    end_time: str
    action: str
    power_kw: float
    energy_kwh: float
    spot_price_per_kwh: float
    deg_cost_per_kwh: float
    net_revenue_dollars: float


def heuristic_optimize(
    soc_current: float,
    soh: float,
    battery_temp: float,
    plug_status: str = "connected",
    horizon_hours: int = HORIZON_HOURS,
    step_minutes: int = STEP_MINUTES,
    spot_prices: Optional[list[float]] = None,
    soc_min: float = 20.0,
    soc_max: float = 90.0,
    soc_departure_target: float = 80.0,
    departure_hour: int = 8,
) -> tuple[list[SlotResult], float, float]:
    """Heuristic V2G arbitrage optimizer.

    Maximises: sum(P_grid * E_dis - P_grid * E_ch - C_deg * |E_dis|)
    subject to:
      - SOC_min <= SOC(t) <= SOC_max
      - SOC(departure) >= soc_departure_target
      - No simultaneous charge and discharge
      - Max charge/discharge power ~7.2 kW (Level 2)

    Returns (schedule, total_revenue, total_deg_cost).
    """
    if plug_status == "disconnected":
        return [], 0.0, 0.0

    if spot_prices is None:
        spot_prices = mock_spot_prices(horizon_hours)

    steps = horizon_hours * 60 // step_minutes
    if steps == 0:
        steps = 1

    deg_params = DegradationParams(soh=soh, battery_temp=battery_temp)
    c_deg = degradation_cost_per_kwh(deg_params)

    power_kw = 7.2  # Level 2 EVSE typical
    soc = soc_current
    schedule: list[SlotResult] = []
    total_revenue = 0.0
    total_deg = 0.0
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    # Determine departure index
    dep_hour_local = departure_hour
    dep_idx = None
    for i in range(steps):
        t = now + timedelta(minutes=i * step_minutes)
        if t.hour == dep_hour_local:
            dep_idx = i
            break
    if dep_idx is None:
        dep_idx = steps - 1  # no departure found, use last step

    for i in range(steps):
        price = spot_prices[i % len(spot_prices)]
        slot_start = now + timedelta(minutes=i * step_minutes)
        slot_end = slot_start + timedelta(minutes=step_minutes)
        energy = power_kw * (step_minutes / 60.0)

        # Determine best action
        # If departure is approaching, prioritize charging to meet target
        steps_until_dep = dep_idx - i
        if steps_until_dep <= 0:
            target_action = "idle"
        elif soc < soc_departure_target and steps_until_dep <= 2:
            # Must charge to meet departure requirement
            target_action = "charge"
        elif price > c_deg and soc > soc_min + 5:
            # Profitable to discharge
            target_action = "discharge"
        elif price < 0.05 and soc < soc_max:
            target_action = "charge"
        else:
            target_action = "idle"

        # Apply constraints
        if target_action == "discharge":
            energy_out = energy
            new_soc = soc - (energy_out / CAPACITY_KWH) * 100.0
            if new_soc < soc_min:
                energy_out = (soc - soc_min) / 100.0 * CAPACITY_KWH
                new_soc = soc_min
            if energy_out <= 0 or c_deg == float("inf"):
                target_action = "idle"
            else:
                soc = new_soc
                rev = energy_out * price - energy_out * c_deg
                total_revenue += rev
                total_deg += energy_out * c_deg
                schedule.append(SlotResult(
                    start_time=slot_start.isoformat(),
                    end_time=slot_end.isoformat(),
                    action="discharge",
                    power_kw=power_kw,
                    energy_kwh=round(energy_out, 3),
                    spot_price_per_kwh=price,
                    deg_cost_per_kwh=round(c_deg, 6),
                    net_revenue_dollars=round(rev, 2),
                ))
        elif target_action == "charge":
            energy_in = energy
            new_soc = soc + (energy_in / CAPACITY_KWH) * 100.0
            if new_soc > soc_max:
                energy_in = (soc_max - soc) / 100.0 * CAPACITY_KWH
                new_soc = soc_max
            if energy_in <= 0:
                target_action = "idle"
            else:
                soc = new_soc
                cost = -energy_in * price
                total_revenue += cost
                schedule.append(SlotResult(
                    start_time=slot_start.isoformat(),
                    end_time=slot_end.isoformat(),
                    action="charge",
                    power_kw=power_kw,
                    energy_kwh=round(energy_in, 3),
                    spot_price_per_kwh=price,
                    deg_cost_per_kwh=0.0,
                    net_revenue_dollars=round(cost, 2),
                ))
        else:
            schedule.append(SlotResult(
                start_time=slot_start.isoformat(),
                end_time=slot_end.isoformat(),
                action="idle",
                power_kw=0.0,
                energy_kwh=0.0,
                spot_price_per_kwh=price,
                deg_cost_per_kwh=0.0,
                net_revenue_dollars=0.0,
            ))

    return schedule, round(total_revenue, 2), round(total_deg, 2)
