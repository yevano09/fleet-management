"""ML scoring over telemetry windows — same candidate-dict contract as legacy (ML-01).

Each candidate: {risk_type, risk_score, confidence, predicted_hours_to_failure,
evidence, recommendation}. Evidence always carries model_version so the UI,
API and eval harness can attribute every prediction.
"""

from __future__ import annotations

import logging

from app.ml.features import FEATURE_GROUPS, series_to_features
from app.ml.model import RISK_MEDIUM, attribute_risk, forest_risk, z_risk

logger = logging.getLogger(__name__)

# Recency window: score the trailing points so a fresh fault isn't diluted by
# stale nominal history. Must match the window length the bundle trained on
# (train/bootstrap scripts use n=WINDOW_POINTS normal series).
WINDOW_POINTS = 24

# Extrapolation targets per risk type: (value key, threshold, worse-is-low?).
_RISK_TARGETS = {
    "signal_degradation": ("signal_strength", -100.0),
    "thermal": ("temperature", 85.0),
    "tire_pressure": ("__tpms_min", 20.0),
    "fuel_anomaly": ("fuel_level_pct", 0.0),
    "dtc_fault": (None, None),
    "intermittent": (None, None),
    "anomaly": (None, None),
}

_RECOMMENDATIONS = {
    "signal_degradation": "ML flag: signal window is anomalous vs fleet baseline. Inspect antenna/radio path.",
    "thermal": "ML flag: thermal window is anomalous vs fleet baseline. Check cooling/ventilation.",
    "tire_pressure": "ML flag: tire-pressure sag vs baseline. Inspect tires for slow puncture.",
    "fuel_anomaly": "ML flag: fuel-drain pattern is anomalous. Check for leaks or sensor fault.",
    "dtc_fault": "ML flag: diagnostic codes present with anomalous context. Pull full DTC snapshot.",
    "intermittent": "ML flag: connectivity/compute window is anomalous. Check power and network.",
    "anomaly": "ML flag: telemetry window is anomalous vs fleet baseline. Investigate.",
}


def _window_series(key: str, points: list[dict]) -> list[float]:
    if key == "__tpms_min":
        out = []
        for p in points:
            tires = p.get("tire_pressures") or {}
            if isinstance(tires, dict) and tires:
                out.append(min(float(v) for v in tires.values() if v is not None))
        return out
    return [float(p[key]) for p in points if p.get(key) is not None]


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    den = sum((x - mean_x) ** 2 for x in range(n))
    if not den:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values)) / den


def ml_score_points(points: list[dict], step_hours: float, bundle: dict) -> list[dict]:
    """Score one device window with the active bundle. Returns candidate dicts."""
    import numpy as np

    if len(points) < 5:
        return []
    window = points[-WINDOW_POINTS:]
    feats = series_to_features(window)
    s = float(bundle["forest"].score_samples(np.asarray([feats]))[0])
    risk = round(max(
        forest_risk(s, bundle["score_mean"], bundle["score_std"]),
        z_risk(feats, bundle["mean"], bundle["std"]),
    ), 3)
    if risk < RISK_MEDIUM:
        return []
    risk_type = attribute_risk(feats, bundle["mean"], bundle["std"])

    htf = None
    target = _RISK_TARGETS.get(risk_type, (None, None))
    if target[0]:
        series = _window_series(target[0], points)
        slope = _slope(series)
        if series and slope != 0:
            if slope < 0 and series[-1] > target[1]:
                htf = round((series[-1] - target[1]) / abs(slope) * step_hours, 1)
            elif slope > 0 and series[-1] < target[1]:
                htf = round((target[1] - series[-1]) / slope * step_hours, 1)

    top_idx = sorted(range(len(feats)),
                     key=lambda i: abs((feats[i] - bundle["mean"][i]) / bundle["std"][i]),
                     reverse=True)[:3]
    return [{
        "risk_type": risk_type,
        "risk_score": risk,
        "confidence": round(min(1.0, len(points) / 20), 3),
        "predicted_hours_to_failure": htf,
        "evidence": {
            "model_version": bundle["version"],
            "anomaly_score": round(s, 4),
            "top_features": [bundle["feature_names"][i] for i in top_idx],
            "samples": len(window),
            "scored_window": WINDOW_POINTS,
        },
        "recommendation": _RECOMMENDATIONS.get(risk_type, _RECOMMENDATIONS["anomaly"]),
    }]
