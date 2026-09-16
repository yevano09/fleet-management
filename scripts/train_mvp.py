"""Train an MVP model on seeded scenarios and register it (LOOP-01 precursor).

Usage:
    python scripts/train_mvp.py [--promote]

Trains IsolationForest on seeded normal windows, saves the artifact under
MODEL_STORAGE_PATH (or ./data/models), runs the backtest gates, and registers
the bundle as staging. With --promote and passing gates, promotes to
production (demoting the current production row).

DATABASE_URL env (or .env) selects the target DB. Requires scikit-learn.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _register(version: str, artifact: str, metrics: dict, promote: bool) -> None:
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.models import MLModel

    async with async_session_factory() as db:
        existing = await db.execute(select(MLModel).where(MLModel.version == version))
        if existing.scalar_one_or_none() is None:
            from app.utils import utcnow

            db.add(MLModel(version=version, artifact_path=artifact,
                           metrics_json=json.dumps(metrics), stage="staging",
                           trained_at=utcnow()))
            await db.commit()
        if promote:
            from app.ml.registry import promote_to_production

            ok = await promote_to_production(db, version)
            print("promoted to production" if ok else "promotion failed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--normals", type=int, default=60)
    args = ap.parse_args()

    from app.ml.features import series_to_features
    from app.ml.model import MODEL_VERSION, train_iforest
    from app.ml.registry import save_bundle
    from app.ml.scenarios import make_series

    base = os.environ.get("MODEL_STORAGE_PATH", "./data/models")
    # Window length must match app.ml.inference.WINDOW_POINTS.
    normals = [series_to_features(make_series("normal", n=24, seed=s)[0])
               for s in range(args.normals)]
    bundle = train_iforest(normals)
    path = save_bundle(bundle, base)
    print(f"trained {bundle['version']} on {len(normals)} normals -> {path}")

    import subprocess

    gates = subprocess.run([sys.executable, "scripts/backtest.py", "--seeds", "10"])
    metrics = {"kind": "seeded-scenarios", "train_normals": len(normals),
               "gates_exit": gates.returncode}
    if gates.returncode != 0 and args.promote:
        print("gates failed — registering as staging only")
        args = argparse.Namespace(promote=False, normals=args.normals)
    asyncio.run(_register(bundle["version"], path, metrics, args.promote))
    return 0 if bundle["version"] == MODEL_VERSION else 1


if __name__ == "__main__":
    raise SystemExit(main())
