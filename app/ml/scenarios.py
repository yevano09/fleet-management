"""Seeded synthetic telemetry series — eval ground truth (EVAL-01).

Each generator mirrors the simulator's physics (`simulator/simulator.py`):
nominal random-walk baselines, then a monotonic fault progression after the
onset index. Deterministic per seed so eval thresholds are reproducible.

Point-dict shape matches what `score_point_series` and `series_to_features`
consume (a subset of Telemetry columns as plain values).
"""

from __future__ import annotations

import random

SCENARIO_KINDS = ("normal", "drift", "thermal", "tpms")

NOMINAL_TIRE_PSI = 32.0


def _nominal_point(rng: random.Random) -> dict:
    return {
        "signal_strength": None,  # filled by walk below
        "temperature": None,
        "cpu_usage": rng.uniform(5, 25),
        "memory_usage": rng.uniform(30, 55),
        "uptime_percentage": 100.0,
        "soh": 100.0,
        "dtc_codes": [],
        "fuel_level_pct": None,
        "tire_pressures": {k: round(rng.uniform(31.0, 33.0), 1) for k in ("fl", "fr", "rl", "rr")},
    }


def make_series(kind: str = "normal", n: int = 48, seed: int = 0) -> tuple[list[dict], int]:
    """Build a labeled series. Returns (points, onset_idx); onset_idx == n for normal."""
    assert kind in SCENARIO_KINDS, f"unknown scenario {kind!r}"
    assert n >= 12, "series too short for windowed features"
    rng = random.Random(seed)
    onset = n if kind == "normal" else n // 3

    signal = rng.uniform(-70, -50)
    temp = rng.uniform(38, 48)
    fuel = rng.uniform(40, 90)
    # Nominal tire pressure is mean-reverting sensor noise around 32 psi
    # (a random walk would wander unphysically far); faults sag monotonically.
    rr_true = NOMINAL_TIRE_PSI
    points: list[dict] = []
    for i in range(n):
        p = _nominal_point(rng)
        signal = max(-95.0, min(-30.0, signal + rng.uniform(-2, 2)))
        temp = max(20.0, min(85.0, temp + rng.uniform(-0.5, 0.5)))
        fuel = max(0.0, fuel - 0.02)
        if i >= onset and kind == "tpms":
            rr_true = max(20.0, rr_true - 0.2)
        rr_t = round(max(20.0, rr_true + rng.uniform(-0.3, 0.3)), 1)
        if i >= onset:
            if kind == "drift":
                signal = max(-100.0, signal - rng.uniform(1.0, 2.0))
            elif kind == "thermal":
                temp = min(96.0, temp + rng.uniform(0.7, 1.1))
        p["signal_strength"] = round(signal, 1)
        p["temperature"] = round(temp, 1)
        p["fuel_level_pct"] = round(fuel, 1)
        p["tire_pressures"]["rr"] = rr_t
        if i >= onset and kind != "normal":
            if kind == "drift" and signal < -85:
                p["dtc_codes"] = ["U0100"]
            elif kind == "thermal" and temp > 75:
                p["dtc_codes"] = ["P0128"]
            elif kind == "tpms" and rr_t < 28.0:
                p["dtc_codes"] = ["C0745"]
        points.append(p)
    return points, onset
