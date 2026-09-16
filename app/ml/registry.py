"""Model registry — artifact persistence + production lookup (ML-01).

Artifacts live under settings.model_storage_path as joblib files; the DB row
is the source of truth for which version is production. Anything that needs a
model calls get_active_bundle(db) and treats None as "use legacy heuristics".
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def artifact_path(version: str, base_dir: str) -> str:
    return os.path.join(base_dir, f"{version}.joblib")


def save_bundle(bundle: dict, base_dir: str) -> str:
    import joblib

    os.makedirs(base_dir, exist_ok=True)
    path = artifact_path(bundle["version"], base_dir)
    joblib.dump(bundle, path)
    return path


def load_bundle(path: str) -> dict | None:
    try:
        import joblib

        bundle = joblib.load(path)
        if not isinstance(bundle, dict) or "forest" not in bundle:
            return None
        return bundle
    except Exception:
        logger.warning("Could not load model bundle %s", path, exc_info=True)
        return None


async def get_active_bundle(db: AsyncSession, base_dir: str) -> dict | None:
    """Return the production bundle, or None when unavailable (legacy fallback)."""
    from app.models import MLModel

    result = await db.execute(
        select(MLModel)
        .where(MLModel.stage == "production")
        .order_by(MLModel.version.desc())
    )
    row = result.scalars().first()
    if row is None:
        return None
    return load_bundle(row.artifact_path or artifact_path(row.version, base_dir))


async def register_model(
    db: AsyncSession,
    version: str,
    artifact: str,
    metrics: dict,
    stage: str = "staging",
) -> object:
    from app.models import MLModel
    from app.utils import utcnow

    row = MLModel(version=version, artifact_path=artifact,
                  metrics_json=__import__("json").dumps(metrics),
                  stage=stage, trained_at=utcnow())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def promote_to_production(db: AsyncSession, version: str) -> bool:
    """Demote current production rows, promote the requested version."""
    from app.models import MLModel

    result = await db.execute(select(MLModel).where(MLModel.version == version))
    row = result.scalar_one_or_none()
    if row is None:
        return False
    old = await db.execute(select(MLModel).where(MLModel.stage == "production"))
    for r in old.scalars().all():
        r.stage = "archived"
    row.stage = "production"
    await db.commit()
    return True
