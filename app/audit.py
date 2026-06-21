"""
Fleet Commander — Audit Log Helper

Records mutating actions for compliance and traceability.
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from datetime import timedelta

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.utils import utcnow
from app.config import settings
from app.metrics import audit_events_total

logger = logging.getLogger(__name__)


async def log_action(
    db: AsyncSession,
    actor: str,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    """Record an audit log entry."""
    entry = AuditLog(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=json.dumps(details or {}),
        ip_address=ip_address,
        timestamp=utcnow(),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    audit_events_total.labels(action=action).inc()
    logger.debug("Audit: %s by %s on %s/%s", action, actor, target_type, target_id)
    return entry


async def get_audit_logs(
    db: AsyncSession,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Fetch paginated audit logs with optional filtering."""
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    if actor:
        query = query.where(AuditLog.actor == actor)
        count_query = count_query.where(AuditLog.actor == actor)
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if target_type:
        query = query.where(AuditLog.target_type == target_type)
        count_query = count_query.where(AuditLog.target_type == target_type)
    if target_id:
        query = query.where(AuditLog.target_id == target_id)
        count_query = count_query.where(AuditLog.target_id == target_id)

    query = query.order_by(AuditLog.timestamp.desc())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(query.offset(offset).limit(limit))
    logs = result.scalars().all()
    return {"logs": logs, "total": total}


async def prune_old_logs(db: AsyncSession, days: int = None) -> int:
    """Delete audit logs older than N days."""
    days = days or settings.audit_log_retention_days
    cutoff = utcnow() - timedelta(days=days)
    result = await db.execute(delete(AuditLog).where(AuditLog.timestamp < cutoff))
    await db.commit()
    return result.rowcount
