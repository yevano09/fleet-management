"""
Fleet Commander — Alert Management API

CRUD for alerts: list, acknowledge, resolve, re-notify, prune.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Body, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.alert_engine import AlertEngine
from app.deps import require_user, require_role
from app.schemas import AlertListResponse, AlertResponse, AcknowledgeRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/", response_model=AlertListResponse)
async def list_alerts(
    status: Optional[str] = Query(None, description="Filter by status: active, acknowledged, resolved"),
    severity: Optional[str] = Query(None, description="Filter by severity: critical, warning, info"),
    alert_type: Optional[str] = Query(None, description="Filter by alert type"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    """List alerts with optional filtering and pagination."""
    engine = AlertEngine(db)
    history = await engine.get_alert_history(
        status=status, severity=severity, alert_type=alert_type,
        limit=limit, offset=offset,
    )
    return {
        "alerts": history.get("alerts", []),
        "total": history.get("total", 0),
    }


@router.get("/active", response_model=AlertListResponse)
async def active_alerts(
    severity: Optional[str] = Query(None, description="Filter by severity: critical, warning, info"),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    """List active (and acknowledged) alerts only."""
    engine = AlertEngine(db)
    alerts = await engine.get_active_alerts(severity=severity)
    return {
        "alerts": alerts,
        "total": len(alerts),
    }


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    req: AcknowledgeRequest = Body(...),
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Acknowledge an active alert."""
    engine = AlertEngine(db)
    ok = await engine.acknowledge_alert(alert_id, req.user)
    if not ok:
        return {"error": "Alert not found or already acknowledged"}
    return {"message": "Alert acknowledged", "alert_id": alert_id, "user": req.user}


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Resolve an alert."""
    engine = AlertEngine(db)
    ok = await engine.resolve_alert(alert_id)
    if not ok:
        return {"error": "Alert not found"}
    return {"message": "Alert resolved", "alert_id": alert_id}


@router.post("/{alert_id}/re-notify")
async def re_notify_alert(
    alert_id: str,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Force re-notification of an alert."""
    from sqlalchemy import select as sel
    from app.models import Alert
    result = await db.execute(sel(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    engine = AlertEngine(db)
    await engine._notify_channels(alert)
    return {"message": "Alert re-notified", "alert_id": alert_id}


@router.delete("/old")
async def prune_old_alerts(
    days: int = Query(7, ge=1, description="Delete resolved alerts older than N days"),
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Prune resolved alerts older than N days."""
    engine = AlertEngine(db)
    deleted = await engine.prune_old_alerts(days=days)
    return {"deleted": deleted, "days": days}
