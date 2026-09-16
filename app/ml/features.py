"""Windowed feature builder — shared by training, inference and eval (ML-01).

Fixed 12-dim vector; missing channels degrade to neutral defaults so mixed
sim/obd/edge series all score. FEATURE_NAMES index order is part of the model
contract — never reorder, only append (bundles store their own copy).
"""

from __future__ import annotations

FEATURE_NAMES = [
    "sig_slope",      # 0  least-squares slope of signal_strength
    "sig_var",        # 1  variance of signal_strength
    "sig_min",        # 2  worst signal in window
    "temp_slope",     # 3  least-squares slope of temperature
    "temp_max",       # 4  hottest sample in window
    "temp_var",       # 5  variance of temperature
    "cpu_var",        # 6  variance of cpu_usage
    "uptime_low",     # 7  fraction of samples with uptime < 95
    "dtc_count",      # 8  number of samples carrying DTC codes
    "fuel_slope",     # 9  least-squares slope of fuel_level_pct
    "tpms_min",       # 10 lowest single-tire pressure in window
    "tpms_sag",       # 11 nominal (32.0) minus tpms_min, floored at 0
]

TPMS_NOMINAL = 32.0

# Feature-index groups used for risk-type attribution (inference.py).
FEATURE_GROUPS = {
    "signal_degradation": (0, 1, 2),
    "thermal": (3, 4, 5),
    "intermittent": (6, 7),
    "dtc_fault": (8,),
    "fuel_anomaly": (9,),
    "tire_pressure": (10, 11),
}

# Groups eligible for the z-gate and attribution. dtc_count and fuel_slope are
# near-constant on normal windows (all-zero / fixed drain), so their
# standardized deviations explode on any jitter and would hijack both the
# gate and the attribution. They stay in the forest feature vector (which
# models the joint distribution) but out of z-logic: DTCs confirm a fault,
# physics names it.
Z_GATE_GROUPS = ("signal_degradation", "thermal", "intermittent", "tire_pressure")


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values))
    den = sum((x - mean_x) ** 2 for x in range(n))
    return num / den if den else 0.0


def _var(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / n


def _col(points: list[dict], key: str) -> list[float]:
    out = []
    for p in points:
        v = p.get(key)
        if v is not None:
            out.append(float(v))
    return out


def _tpms_min(points: list[dict]) -> float:
    best = None
    for p in points:
        tires = p.get("tire_pressures") or {}
        if isinstance(tires, str):
            continue  # DB rows carry JSON text; parsed by callers, not here
        for v in tires.values():
            if v is not None and (best is None or float(v) < best):
                best = float(v)
    return best if best is not None else TPMS_NOMINAL


def series_to_features(points: list[dict]) -> list[float]:
    """Collapse a telemetry window to the fixed 12-dim feature vector."""
    sig = _col(points, "signal_strength")
    tmp = _col(points, "temperature")
    cpu = _col(points, "cpu_usage")
    upt = _col(points, "uptime_percentage")
    fuel = _col(points, "fuel_level_pct")
    dtc = sum(1 for p in points if p.get("dtc_codes"))
    tpms_min = _tpms_min(points)
    return [
        _slope(sig),
        _var(sig),
        min(sig) if sig else -60.0,
        _slope(tmp),
        max(tmp) if tmp else 45.0,
        _var(tmp),
        _var(cpu),
        sum(1 for u in upt if u < 95.0) / len(upt) if upt else 0.0,
        float(dtc),
        _slope(fuel),
        tpms_min,
        max(0.0, TPMS_NOMINAL - tpms_min),
    ]
