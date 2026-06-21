"""
Fleet Commander — Audit Log API (Feature 6)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.audit import get_audit_logs, prune_old_logs
from app.schemas import AuditLogResponse, AuditLogListResponse

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    actor: str = Query(None),
    action: str = Query(None),
    target_type: str = Query(None),
    target_id: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    result = await get_audit_logs(db, actor=actor, action=action, target_type=target_type,
                                  target_id=target_id, limit=limit, offset=offset)
    return AuditLogListResponse(
        logs=[AuditLogResponse.model_validate(l) for l in result["logs"]],
        total=result["total"],
    )


@router.delete("/old")
async def prune_audit_logs(days: int = Query(90, ge=1), db: AsyncSession = Depends(get_db)):
    deleted = await prune_old_logs(db, days=days)
    return {"deleted": deleted, "days": days}
