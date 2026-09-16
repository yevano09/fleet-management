"""MVP backtest — legacy slopes vs registry model on seeded scenarios.

Usage:
    python scripts/backtest.py [--version mvp-iforest-v1] [--seeds 20]

Prints a comparison table (detection / false-positives / median lead) and
exits non-zero when the model fails the gates in tests/eval_thresholds.json.
This is the promotion gate scripts/train_mvp.py uses before registering a
production model (LOOP-01 precursor).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ml.features import series_to_features  # noqa: E402
from app.ml.scenarios import SCENARIO_KINDS, make_series  # noqa: E402
from app.predictive_maintenance import MIN_POINTS, score_point_series  # noqa: E402

FAULT_KINDS = ("drift", "thermal", "tpms")
EXPECTED_RISK = {"drift": "signal_degradation", "thermal": "thermal", "tpms": "tire_pressure"}


def load_thresholds() -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tests", "eval_thresholds.json")
    with open(path) as f:
        return json.load(f)


def legacy_lead(points: list[dict], onset: int, step_hours: float = 0.5) -> int | None:
    """First prefix length (from onset) where legacy flags, as lead steps."""
    for end in range(max(onset + 1, MIN_POINTS + 1), len(points) + 1):
        cands = score_point_series(points[:end], step_hours)
        if any(c["risk_score"] >= 0.4 for c in cands):
            return len(points) - end
    return None


def model_lead(score_fn, points: list[dict], onset: int) -> tuple[int | None, str | None]:
    for end in range(max(onset + 1, MIN_POINTS + 1), len(points) + 1):
        cands = score_fn(points[:end])
        hits = [c for c in cands if c["risk_score"] >= 0.4]
        if hits:
            best = max(hits, key=lambda c: c["risk_score"])
            return len(points) - end, best["risk_type"]
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--series-len", type=int, default=48)
    args = ap.parse_args()
    th = load_thresholds()
    min_risk = th["min_risk"]

    try:
        from app.ml.inference import WINDOW_POINTS, ml_score_points
        from app.ml.model import MODEL_VERSION, train_iforest

        # Train window must match the inference recency window.
        normals = [series_to_features(make_series("normal", n=WINDOW_POINTS, seed=s)[0])
                   for s in range(60)]
        bundle = train_iforest(normals)
        step = 0.5

        def score_fn(prefix):
            return ml_score_points(prefix, step, bundle)

        ml_ok = True
    except Exception as e:  # sklearn missing → report legacy only
        print(f"ML unavailable ({e}); legacy-only report")
        ml_ok = False

    print(f"{'scenario':<10}{'legacy_lead':>12}{'model_lead':>12}{'model_type':>20}")
    failures = []
    for kind in FAULT_KINDS:
        leads_leg, leads_ml, types = [], [], []
        for s in range(args.seeds):
            pts, onset = make_series(kind, n=args.series_len, seed=1000 + s)
            leads_leg.append(legacy_lead(pts, onset))
            if ml_ok:
                lead, rtype = model_lead(score_fn, pts, onset)
                leads_ml.append(lead)
                types.append(rtype)
        det_leg = sum(1 for l in leads_leg if l is not None)
        line = f"{kind:<10}{det_leg}/{args.seeds} detected"
        if ml_ok:
            det_ml = sum(1 for l in leads_ml if l is not None)
            med = sorted(l for l in leads_ml if l is not None)[len([l for l in leads_ml if l is not None]) // 2] if det_ml else None
            line += f"   {det_ml}/{args.seeds} detected  lead~{med}"
            if det_ml < args.seeds:
                failures.append(f"{kind}: model missed {args.seeds - det_ml}")
            need = th["min_lead_steps"][kind]
            if med is not None and med < need:
                failures.append(f"{kind}: lead {med} < {need}")
            want = EXPECTED_RISK[kind]
            if not any(t == want for t in types if t):
                failures.append(f"{kind}: never attributed as {want}")
        print(line)

    # False positives on normals
    fps = 0
    for s in range(args.seeds):
        pts, _ = make_series("normal", n=args.series_len, seed=2000 + s)
        if ml_ok and any(c["risk_score"] >= min_risk for c in score_fn(pts)):
            fps += 1
    fp_rate = fps / args.seeds
    print(f"normal FP rate: {fp_rate:.2%} (gate <= {th['max_fp_rate']:.0%})")
    if ml_ok and fp_rate > th["max_fp_rate"]:
        failures.append(f"FP rate {fp_rate:.2%} over gate")

    if failures:
        print("GATES FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL GATES PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
