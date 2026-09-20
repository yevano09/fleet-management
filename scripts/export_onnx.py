"""Export a registry IsolationForest bundle to an edge pack (P0-E).

Produces, under MODEL_STORAGE_PATH/edge/<version>/:
  model.onnx        IsolationForest score_samples as an ONNX graph
  thresholds.json   feature names/mean/std + score calibration + z-gate elbows

The edge gateway (edge/gateway.py) and the documented ESP32 threshold engine
use thresholds.json; model.onnx is used when onnxruntime is available.
Requires: pip install skl2onnx onnx  (kept OUT of backend requirements).

Usage:
    python scripts/export_onnx.py [--version mvp-iforest-v1] [--normals 60]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="mvp-iforest-v1")
    ap.add_argument("--normals", type=int, default=60)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    try:
        import numpy as _np  # noqa: F401
        from skl2onnx import convert_sklearn
        from skl2onnx.common.data_types import FloatTensorType
        import onnxruntime as _ort
    except ImportError:
        print("export needs: pip install skl2onnx onnx onnxruntime scikit-learn")
        return 2

    from app.ml.features import FEATURE_NAMES, series_to_features
    from app.ml.model import MODEL_VERSION, train_iforest
    from app.ml.scenarios import make_series

    normals = [series_to_features(make_series("normal", n=24, seed=s)[0])
               for s in range(args.normals)]
    bundle = train_iforest(normals)
    if bundle["version"] != args.version:
        print(f"note: trained {bundle['version']} (requested {args.version})")

    initial = [("input", FloatTensorType([None, len(FEATURE_NAMES)]))]
    # ai.onnx.ml v4 (emitted by newer skl2onnx) outruns most runtimes;
    # pin v3, which every onnxruntime >= 1.10 reads.
    onnx_model = convert_sklearn(bundle["forest"], initial_types=initial,
                                 target_opset={"": 18, "ai.onnx.ml": 3})

    base = args.out or os.path.join(
        os.environ.get("MODEL_STORAGE_PATH", "./data/models"),
        "edge", bundle["version"])
    os.makedirs(base, exist_ok=True)
    onnx_path = os.path.join(base, "model.onnx")
    with open(onnx_path, "wb") as f:
        f.write(onnx_model.SerializeToString())

    # Align ONNX score space to sklearn score_samples: skl2onnx emits a
    # shifted 'scores' output (constant offset, verified empirically). Measure
    # it on the training normals instead of assuming the constant.
    import numpy as _np2

    sess = _ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    X = _np2.asarray(normals, dtype=_np2.float32)
    sk = bundle["forest"].score_samples(X)
    ox = sess.run(None, {"input": X})[1].ravel()
    shift = float((sk - ox).mean())
    spread = float(abs(sk - (ox + shift)).max())
    print(f"onnx shift: {shift:.5f} (max abs residual {spread:.2e})")
    assert spread < 1e-4, f"ONNX/sklearn divergence too large: {spread}"
    pack = {
        "version": bundle["version"],
        "feature_names": bundle["feature_names"],
        "mean": bundle["mean"],
        "std": bundle["std"],
        "score_mean": bundle["score_mean"],
        "score_std": bundle["score_std"],
        "onnx_score_shift": shift,
        "forest_threshold_sigma": 2.5,
        "forest_span_sigma": 5.0,
        "z_quiet": 3.0,
        "z_full": 6.0,
        "risk_medium": 0.4,
        "window_points": 24,
        "kind": "iforest-hybrid-v1",
    }
    with open(os.path.join(base, "thresholds.json"), "w") as f:
        json.dump(pack, f, indent=2)
    print(f"edge pack -> {base} (model.onnx + thresholds.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
