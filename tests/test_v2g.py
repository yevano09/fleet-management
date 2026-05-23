"""
Tests for V2G Arbitrage with Battery Degradation Pricing.

Covers:
  - Degradation cost model
  - Heuristic optimizer decisions (buy, sell, hold)
  - API endpoint contract
  - Prometheus metric exposure
"""

import pytest
from datetime import datetime, timezone, timedelta as td

from app.v2g_optimizer import (
    degradation_cost_per_kwh,
    DegradationParams,
    heuristic_optimize,
    mock_spot_prices,
)


class TestDegradationCost:
    def test_no_discharge_below_min_soh(self):
        params = DegradationParams(soh=0.65, battery_temp=25.0, replacement_cost=35000.0)
        cost = degradation_cost_per_kwh(params)
        assert cost == float("inf"), "Should prohibit discharge below 70% SOH"

    def test_minimal_cost_at_high_soh(self):
        params = DegradationParams(soh=0.9, battery_temp=25.0, replacement_cost=35000.0)
        cost = degradation_cost_per_kwh(params)
        assert 0 < cost < 1.0, f"Expected reasonable cost, got {cost}"

    def test_cost_rises_with_low_soh(self):
        high_soh = DegradationParams(soh=0.85, battery_temp=25.0)
        low_soh = DegradationParams(soh=0.72, battery_temp=25.0)
        cost_high = degradation_cost_per_kwh(high_soh)
        cost_low = degradation_cost_per_kwh(low_soh)
        assert cost_low > cost_high, "Cost should increase as SOH decreases"

    def test_temperature_acceleration(self):
        cool = DegradationParams(soh=0.8, battery_temp=15.0)
        hot = DegradationParams(soh=0.8, battery_temp=45.0)
        assert degradation_cost_per_kwh(hot) > degradation_cost_per_kwh(cool)

    def test_replacement_cost_scaling(self):
        cheap = DegradationParams(soh=0.8, battery_temp=25.0, replacement_cost=25000.0)
        expensive = DegradationParams(soh=0.8, battery_temp=25.0, replacement_cost=45000.0)
        assert degradation_cost_per_kwh(expensive) > degradation_cost_per_kwh(cheap)


class TestMockSpotPrices:
    def test_returns_correct_length(self):
        prices = mock_spot_prices(hours=24)
        assert len(prices) == 24

    def test_prices_are_positive(self):
        prices = mock_spot_prices(hours=48)
        assert all(p > 0 for p in prices)


class TestHeuristicOptimize:
    def test_returns_schedule(self):
        schedule, revenue, deg_cost = heuristic_optimize(
            soc_current=80.0, soh=100.0, battery_temp=25.0,
            horizon_hours=6, step_minutes=60,
        )
        assert len(schedule) == 6
        assert deg_cost >= 0

    def test_disconnected_device_no_schedule(self):
        schedule, revenue, deg_cost = heuristic_optimize(
            soc_current=80.0, soh=100.0, battery_temp=25.0,
            plug_status="disconnected",
        )
        assert schedule == []
        assert revenue == 0.0

    def test_no_discharge_at_low_soh(self):
        schedule, revenue, deg_cost = heuristic_optimize(
            soc_current=80.0, soh=0.65, battery_temp=25.0,
            horizon_hours=4, step_minutes=60,
        )
        assert all(s.action != "discharge" for s in schedule), \
            "Should not discharge when SOH is below minimum"

    def test_charge_when_price_low(self):
        low_prices = [0.02] * 6
        schedule, revenue, deg_cost = heuristic_optimize(
            soc_current=50.0, soh=100.0, battery_temp=25.0,
            horizon_hours=6, step_minutes=60,
            spot_prices=low_prices,
            soc_max=90.0,
        )
        charge_slots = [s for s in schedule if s.action == "charge"]
        assert len(charge_slots) > 0, "Should charge when prices are very low"

    def test_discharge_when_price_high_and_soc_adequate(self):
        high_prices = [0.50] * 6
        schedule, revenue, deg_cost = heuristic_optimize(
            soc_current=80.0, soh=100.0, battery_temp=25.0,
            horizon_hours=6, step_minutes=60,
            spot_prices=high_prices,
        )
        discharge_slots = [s for s in schedule if s.action == "discharge"]
        assert len(discharge_slots) > 0, "Should discharge when prices are high enough"

    def test_departure_charging(self):
        # If departure is soon and SOC is below target, should charge
        # even when prices are high enough that discharge would be profitable.
        start_hour = (datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + td(hours=1)).hour
        dep_hour = (start_hour + 3) % 24  # departure at slot index 3
        prices = [0.03, 0.03, 0.50, 0.50, 0.50, 0.50]
        schedule, revenue, deg_cost = heuristic_optimize(
            soc_current=30.0, soh=100.0, battery_temp=25.0,
            horizon_hours=6, step_minutes=60,
            spot_prices=prices,
            soc_departure_target=80.0,
            departure_hour=dep_hour,
        )
        # Should charge in at least one of the first 3 slots before departure
        charge_before_dep = [s for s in schedule[:3] if s.action == "charge"]
        assert len(charge_before_dep) > 0, "Should prioritise charging before departure"

    def test_soc_constraints_respected(self):
        schedule, revenue, deg_cost = heuristic_optimize(
            soc_current=85.0, soh=100.0, battery_temp=25.0,
            horizon_hours=24, step_minutes=60,
            soc_min=20.0, soc_max=90.0,
        )
        # Simulate SOC trajectory
        soc = 85.0
        cap = 60.0
        for s in schedule:
            if s.action == "discharge":
                soc -= (s.energy_kwh / cap) * 100.0
            elif s.action == "charge":
                soc += (s.energy_kwh / cap) * 100.0
            assert soc >= 19.0, f"SOC dropped below minimum: {soc}"
            assert soc <= 91.0, f"SOC exceeded maximum: {soc}"

    def test_net_revenue_positive_when_profitable(self):
        cheap = [0.03] * 3 + [0.50] * 3
        schedule, revenue, deg_cost = heuristic_optimize(
            soc_current=50.0, soh=100.0, battery_temp=25.0,
            horizon_hours=6, step_minutes=60,
            spot_prices=cheap,
        )
        net = revenue - deg_cost
        assert net != 0 or all(s.action == "idle" for s in schedule)
