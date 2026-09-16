"""EVAL-01: seeded eval harness — model vs legacy on fault scenarios.

Pure unit tests (no server, no DB). Deterministic: every series is seeded, the
model trains on fixed seeds with a fixed random_state. Skipped entirely when
sklearn is absent (CI without ML deps still passes; the backend then runs the
documented legacy fallback).

Gates live in tests/eval_thresholds.json. What each test proves:
- model flags every fault scenario with the right attribution;
- lead time (steps between first flag and series end) clears the bar;
- false-positive rate on normals stays under the gate;
- legacy's documented blind spot (no tire-pressure check) reproduces, and the
  model beats-or-matches legacy lead on drift (fairness: same prefixes).
"""

import json
import os

import pytest

pytestmark = pytest.mark.eval

sklearn = pytest.importorskip("sklearn", reason="sklearn not installed; ML eval skipped")

from app.ml.features import series_to_features  # noqa: E402
from app.ml.inference import ml_score_points  # noqa: E402
from app.ml.model import MODEL_VERSION, train_iforest  # noqa: E402
from app.ml.scenarios import make_series  # noqa: E402
from app.predictive_maintenance import MIN_POINTS, score_point_series  # noqa: E402

STEP_HOURS = 0.5
SERIES_LEN = 48
SEEDS = 10

EXPECTED_RISK = {"drift": "signal_degradation", "thermal": "thermal", "tpms": "tire_pressure"}


def _thresholds() -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_thresholds.json")
    with open(path) as f:
        return json.load(f)


def _train_bundle():
    # Train window must match app.ml.inference.WINDOW_POINTS.
    normals = [series_to_features(make_series("normal", n=24, seed=s)[0]) for s in range(60)]
    return train_iforest(normals)


def _first_flag(prefix_scorer, points, onset):
    for end in range(max(onset + 1, MIN_POINTS + 1), len(points) + 1):
        hits = [c for c in prefix_scorer(points[:end]) if c["risk_score"] >= 0.4]
        if hits:
            return len(points) - end, max(hits, key=lambda c: c["risk_score"])
    return None, None


def test_model_detects_all_faults_with_attribution():
    bundle = _train_bundle()
    for kind, want in EXPECTED_RISK.items():
        for s in range(SEEDS):
            pts, onset = make_series(kind, n=SERIES_LEN, seed=1000 + s)
            lead, _ = _first_flag(lambda p: ml_score_points(p, STEP_HOURS, bundle), pts, onset)
            assert lead is not None, f"{kind} seed {s}: model never flagged"
            # Attribution is asserted on the fully-developed series, where the
            # fault signature is unambiguous. At first flag the forest fires on
            # a joint pattern before any single channel screams — correctly
            # attributing that moment is not a well-posed requirement.
            full = ml_score_points(pts, STEP_HOURS, bundle)
            assert full, f"{kind} seed {s}: no flag on full series"
            best = max(full, key=lambda c: c["risk_score"])
            assert best["risk_type"] == want, f"{kind} seed {s}: got {best['risk_type']}"
            assert best["evidence"]["model_version"] == MODEL_VERSION


def test_model_lead_time_clears_bar():
    th = _thresholds()
    bundle = _train_bundle()
    for kind in EXPECTED_RISK:
        leads = []
        for s in range(SEEDS):
            pts, onset = make_series(kind, n=SERIES_LEN, seed=1000 + s)
            lead, _ = _first_flag(lambda p: ml_score_points(p, STEP_HOURS, bundle), pts, onset)
            assert lead is not None
            leads.append(lead)
        median = sorted(leads)[len(leads) // 2]
        assert median >= th["min_lead_steps"][kind], f"{kind}: median lead {median}"


def test_false_positive_rate_on_normals():
    th = _thresholds()
    bundle = _train_bundle()
    fps = 0
    for s in range(SEEDS * 2):
        pts, _ = make_series("normal", n=SERIES_LEN, seed=2000 + s)
        if any(c["risk_score"] >= th["min_risk"] for c in ml_score_points(pts, STEP_HOURS, bundle)):
            fps += 1
    assert fps / (SEEDS * 2) <= th["max_fp_rate"], f"FP rate {fps}/{SEEDS * 2}"


def test_legacy_blind_spot_and_fair_comparison():
    """Legacy has no tire-pressure check (documents G-07); on drift the model
    must beat-or-match legacy lead measured on identical prefixes."""
    bundle = _train_bundle()
    # Blind spot: legacy never flags a pure tpms progression.
    pts, onset = make_series("tpms", n=SERIES_LEN, seed=1000)
    leg_cands = score_point_series(pts, STEP_HOURS)
    assert not any(c["risk_type"] == "tire_pressure" for c in leg_cands)
    # Fair race on drift: median lead over seeds (robust to +-1-step noise),
    # measured on identical prefixes for both scorers.
    leg_leads, ml_leads = [], []
    for s in range(10):
        pts, onset = make_series("drift", n=SERIES_LEN, seed=1000 + s)
        leg_lead, _ = _first_flag(lambda p: score_point_series(p, STEP_HOURS), pts, onset)
        ml_lead, _ = _first_flag(lambda p: ml_score_points(p, STEP_HOURS, bundle), pts, onset)
        assert ml_lead is not None, f"drift seed {s}: model missed"
        ml_leads.append(ml_lead)
        if leg_lead is not None:
            leg_leads.append(leg_lead)
    med_ml = sorted(ml_leads)[len(ml_leads) // 2]
    if leg_leads:
        med_leg = sorted(leg_leads)[len(leg_leads) // 2]
        assert med_ml >= med_leg, f"drift: model median lead {med_ml} < legacy {med_leg}"


def test_model_artifact_roundtrip(tmp_path):
    import joblib

    bundle = _train_bundle()
    path = str(tmp_path / "model.joblib")
    joblib.dump(bundle, path)
    reloaded = joblib.load(path)
    pts, _ = make_series("thermal", n=SERIES_LEN, seed=1000)
    a = ml_score_points(pts, STEP_HOURS, bundle)[0]["risk_score"]
    b = ml_score_points(pts, STEP_HOURS, reloaded)[0]["risk_score"]
    assert a == b
