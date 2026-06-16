import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session_factory
from app.utils import utcnow
from app.aegis.models import Remediation
from app.aegis.schemas import RemediationResponse, RemediationListResponse, IngestRequest
from app.aegis.engine import AegisEngine
from app.aegis.rules import build_default_registry
from app.aegis.config import AEGIS_DEFAULT_SCRAPE_INTERVAL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/aegis", tags=["aegis"])


def _remediation_to_dict(r: Remediation) -> dict:
    return {
        "id": r.id,
        "signal_id": r.signal_id,
        "metric_name": r.metric_name,
        "value": r.value,
        "threshold": r.threshold,
        "severity": r.severity,
        "rule_name": r.rule_name,
        "action_name": r.action_name,
        "status": r.status,
        "input_snapshot": r.input_snapshot,
        "output_snapshot": r.output_snapshot,
        "error_message": r.error_message,
        "started_at": r.started_at,
        "completed_at": r.completed_at,
        "duration_ms": r.duration_ms,
        "retry_count": r.retry_count,
        "device_ids": r.device_ids,
    }


@router.get("/history", response_model=RemediationListResponse)
async def get_remediation_history(
    status: Optional[str] = Query(None, description="Filter by status: pending, in_progress, success, failed, dlq, escalated"),
    action: Optional[str] = Query(None, description="Filter by action name"),
    metric: Optional[str] = Query(None, description="Filter by metric name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Remediation)
    count_query = select(func.count()).select_from(Remediation)

    if status:
        query = query.where(Remediation.status == status)
        count_query = count_query.where(Remediation.status == status)
    if action:
        query = query.where(Remediation.action_name == action)
        count_query = count_query.where(Remediation.action_name == action)
    if metric:
        query = query.where(Remediation.metric_name == metric)
        count_query = count_query.where(Remediation.metric_name == metric)

    query = query.order_by(Remediation.started_at.desc())

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(query.offset(offset).limit(limit))
    remediations = result.scalars().all()

    return RemediationListResponse(
        remediations=[RemediationResponse.model_validate(r) for r in remediations],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/history/{remediation_id}", response_model=RemediationResponse)
async def get_remediation_detail(
    remediation_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Remediation).where(Remediation.id == remediation_id))
    remediation = result.scalar_one_or_none()
    if not remediation:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Remediation not found")
    return RemediationResponse.model_validate(remediation)


@router.post("/ingest")
async def ingest_alert(
    req: IngestRequest,
    db: AsyncSession = Depends(get_db),
):
    registry = build_default_registry()
    engine = AegisEngine(registry=registry)
    signal = await engine.process_ingest(db, req)
    return {
        "message": "Alert ingested and processed",
        "signal_id": signal.id,
        "metric_name": signal.metric_name,
        "severity": signal.severity,
    }


@router.delete("/history")
async def prune_history(
    older_than_days: int = Query(90, ge=1, description="Delete records older than N days"),
    db: AsyncSession = Depends(get_db),
):
    from datetime import timedelta
    cutoff = utcnow() - timedelta(days=older_than_days)
    result = await db.execute(
        delete(Remediation).where(Remediation.started_at < cutoff)
    )
    await db.commit()
    return {"deleted": result.rowcount, "older_than_days": older_than_days}
