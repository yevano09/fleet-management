"""Seeded bootstrap model — zero-ops MVP default (ML-01).

On backend boot, if no production row exists, train the documented stand-in
model on seeded normal series and register it as production. Training is
milliseconds (200 trees x ~60 samples); the whole thing is skipped when a
production model already exists, so real trained models are never clobbered.
Replace via scripts/train_mvp.py on real data (LOOP-01).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def ensure_default_model(db: AsyncSession, base_dir: str) -> str | None:
    """Return the active production version, bootstrapping the seeded default if needed."""
    from sqlalchemy import select

    from app.models import MLModel

    existing = await db.execute(select(MLModel).where(MLModel.stage == "production"))
    if existing.scalars().first() is not None:
        return None  # a real model owns production; leave it alone
    try:
        from app.ml.features import series_to_features
        from app.ml.model import MODEL_VERSION, train_iforest
        from app.ml.registry import promote_to_production, register_model, save_bundle
        from app.ml.scenarios import make_series

        # Window length must match inference.WINDOW_POINTS.
        normals = [series_to_features(make_series("normal", n=24, seed=s)[0]) for s in range(60)]
        bundle = train_iforest(normals)
        path = save_bundle(bundle, base_dir)
        row = await register_model(
            db, bundle["version"], path,
            {"kind": "seeded-bootstrap", "train_normals": len(normals),
             "note": "stand-in until LOOP-01 trains on real fleet data"},
            stage="staging",
        )
        await promote_to_production(db, row.version)
        logger.info("Bootstrapped seeded ML model %s", bundle["version"])
        return bundle["version"]
    except Exception:
        logger.warning("ML bootstrap unavailable (sklearn missing?) — legacy heuristics active",
                       exc_info=True)
        return None
