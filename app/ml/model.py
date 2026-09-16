"""IsolationForest anomaly model — training + scoring (ML-01).

Design: one-class model over NORMAL telemetry windows (trailing-24 recency
windows; see inference.WINDOW_POINTS). Anything far from the normal manifold
scores as anomalous; attribution picks the feature group with the largest
standardized deviation, which maps to a risk_type. Deterministic: fixed
random_state and seeded training data.

Hybrid risk (documented, tunable via the Z_* constants):
- forest side: train scores s (higher = more normal) with mean/std over the
  train set; threshold = mean - 2*std, full-scale span 4*std below it.
- z side: max standardized feature deviation; <= Z_QUIET is silence,
  Z_QUIET..Z_FULL maps to RISK_MEDIUM..1.0. This catches sustained drifts
  whose forest score moves slowly but whose slope features leave the normal
  envelope fast.
The 0.4 floor matches legacy RISK_MEDIUM so flags are comparable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import numpy as np
    from sklearn.ensemble import IsolationForest

    SKLEARN_OK = True
except Exception:  # pragma: no cover — degraded path when sklearn absent
    np = None  # type: ignore
    IsolationForest = None  # type: ignore
    SKLEARN_OK = False

from app.ml.features import FEATURE_NAMES, FEATURE_GROUPS, Z_GATE_GROUPS

RISK_MEDIUM = 0.4
N_ESTIMATORS = 200
RANDOM_STATE = 7
MODEL_VERSION = "mvp-iforest-v1"
# z-gate elbows (see module docstring). Raise Z_QUIET to cut FPs, lower to gain lead.
Z_QUIET = 3.0
Z_FULL = 6.0


def train_iforest(normal_matrix: list[list[float]]) -> dict:
    """Fit on normal-only feature rows. Returns a versioned bundle dict."""
    if not SKLEARN_OK:
        raise RuntimeError("sklearn is not installed; cannot train")
    X = np.asarray(normal_matrix, dtype=float)
    mean = X.mean(axis=0)
    std = X.std(axis=0) + 1e-9
    forest = IsolationForest(
        n_estimators=N_ESTIMATORS, contamination=0.02, random_state=RANDOM_STATE
    )
    forest.fit(X)
    train_scores = forest.score_samples(X)
    return {
        "version": MODEL_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "mean": mean.tolist(),
        "std": std.tolist(),
        "score_mean": float(train_scores.mean()),
        "score_std": float(train_scores.std()) + 1e-9,
        "forest": forest,
    }


def forest_risk(s: float, score_mean: float, score_std: float) -> float:
    """Risk from the IsolationForest score alone (conservative tail gate)."""
    thr = score_mean - 2.5 * score_std
    if s >= thr:
        return 0.0
    return min(1.0, RISK_MEDIUM + (1.0 - RISK_MEDIUM) * (thr - s) / (5.0 * score_std))


def z_risk(features: list[float], mean: list[float], std: list[float]) -> float:
    """Risk from the largest standardized deviation within gate groups."""
    z = _group_max_z(features, mean, std)
    if z <= Z_QUIET:
        return 0.0
    return min(1.0, RISK_MEDIUM + (1.0 - RISK_MEDIUM) * (z - Z_QUIET) / (Z_FULL - Z_QUIET))


def _group_max_z(features: list[float], mean: list[float], std: list[float]) -> float:
    best = 0.0
    for group in Z_GATE_GROUPS:
        for i in FEATURE_GROUPS[group]:
            z = abs((features[i] - mean[i]) / std[i])
            if z > best:
                best = z
    return best


def attribute_risk(features: list[float], mean: list[float], std: list[float]) -> str:
    """Pick the gate group with the largest standardized deviation."""
    best_group, best_z = "anomaly", 0.0
    for group in Z_GATE_GROUPS:
        idxs = FEATURE_GROUPS[group]
        z = max(abs((features[i] - mean[i]) / std[i]) for i in idxs)
        if z > best_z:
            best_z, best_group = z, group
    return best_group
