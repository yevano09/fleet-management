"""
Fleet Commander — Predictive Maintenance API (Feature 3)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.predictive_maintenance import run_prediction_cycle, get_predictions
from app.schemas import PredictedFailureResponse, PredictedFailureListResponse
from app.deps import require_user, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictive", tags=["predictive-maintenance"])


@router.get("/predictions", response_model=PredictedFailureListResponse)
async def list_predictions(
    device_id: Optional[str] = Query(None),
    resolved: Optional[bool] = Query(False),
    min_risk: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    result = await get_predictions(db, device_id=device_id, resolved=resolved, min_risk=min_risk,
                                   limit=limit, offset=offset)
    return PredictedFailureListResponse(
        predictions=[PredictedFailureResponse.model_validate(p) for p in result["predictions"]],
        total=result["total"],
    )


@router.post("/scan")
async def run_predictive_scan(principal: dict = Depends(require_role("operator")), db: AsyncSession = Depends(get_db)):
    """Trigger a predictive maintenance analysis cycle across all online devices."""
    predictions = await run_prediction_cycle(db)
    return {
        "message": f"Predictive scan completed. {len(predictions)} predictions generated.",
        "predictions_count": len(predictions),
        "predictions": [
            {
                "device_id": p.device_id,
                "risk_type": p.risk_type,
                "risk_score": p.risk_score,
                "recommendation": p.recommendation,
            }
            for p in predictions
        ],
    }


@router.post("/predictions/{prediction_id}/resolve")
async def resolve_prediction(
    prediction_id: str,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Mark a predicted failure as resolved (addressed by maintenance)."""
    from app.models import PredictedFailure
    from sqlalchemy import select
    result = await db.execute(select(PredictedFailure).where(PredictedFailure.id == prediction_id))
    pred = result.scalar_one_or_none()
    if not pred:
        raise HTTPException(status_code=404, detail="Prediction not found")
    pred.resolved = True
    await db.commit()
    from app.metrics import predicted_failures_active
    predicted_failures_active.dec()
    return {"message": "Prediction resolved", "prediction_id": prediction_id}
