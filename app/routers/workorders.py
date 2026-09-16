"""
Fleet Commander — Work Order API (MVP WO-01)

Alert -> work order loop: list/open/close maintenance tickets. Auto-creation
on alert escalation lives in app/alert_engine.py; these endpoints cover the
manual path plus lifecycle transitions.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role, require_user, allowed_orgs
from app.models import Alert, WorkOrder, WorkOrderStatus
from app.schemas import (
    WorkOrderCreateRequest,
    WorkOrderCloseRequest,
    WorkOrderResponse,
    WorkOrderListResponse,
)
from app.utils import utcnow
from app.metrics import workorders_total, workorder_close_latency_seconds
from app.alert_engine import WORKORDER_TEMPLATES, DEFAULT_WO_TEMPLATE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workorders", tags=["workorders"])


def _scope(query, principal):
    orgs = allowed_orgs(principal)
    if orgs is not None:
        query = query.where(WorkOrder.org_id.in_(orgs))
    return query


@router.get("", response_model=WorkOrderListResponse)
async def list_work_orders(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = _scope(select(WorkOrder), principal)
    count_q = _scope(select(func.count()).select_from(WorkOrder), principal)
    if status:
        query = query.where(WorkOrder.status == WorkOrderStatus(status))
        count_q = count_q.where(WorkOrder.status == WorkOrderStatus(status))
    total = (await db.execute(count_q)).scalar() or 0
    rows = (await db.execute(query.order_by(WorkOrder.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return WorkOrderListResponse(
        work_orders=[WorkOrderResponse.model_validate(w) for w in rows], total=total
    )


@router.post("", response_model=WorkOrderResponse, status_code=201)
async def create_work_order(
    req: WorkOrderCreateRequest,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Manually open a work order, optionally linked to an alert."""
    alert = None
    if req.alert_id:
        result = await db.execute(select(Alert).where(Alert.id == req.alert_id))
        alert = result.scalar_one_or_none()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        if alert.work_order_id:
            existing = await db.execute(select(WorkOrder).where(WorkOrder.id == alert.work_order_id))
            wo = existing.scalar_one_or_none()
            if wo:
                return WorkOrderResponse.model_validate(wo)  # idempotent
    tpl = WORKORDER_TEMPLATES.get(alert.type if alert else "", DEFAULT_WO_TEMPLATE)
    orgs = allowed_orgs(principal)
    wo = WorkOrder(
        alert_id=req.alert_id,
        device_ids=",".join(req.device_ids) if req.device_ids else (alert.device_ids if alert else ""),
        title=req.title or tpl["title"],
        detail=req.detail or tpl["detail"],
        severity=req.severity or (alert.severity if alert else "warning"),
        status=WorkOrderStatus.open,
        assignee=req.assignee,
        cost_estimate=req.cost_estimate,
        org_id=(orgs[0] if orgs else (alert.org_id if alert else "org-default")),
    )
    db.add(wo)
    await db.commit()
    await db.refresh(wo)
    if alert:
        alert.work_order_id = wo.id
        await db.commit()
    workorders_total.labels(status="open").inc()
    logger.info("Work order %s opened (alert=%s)", wo.id, req.alert_id)
    return WorkOrderResponse.model_validate(wo)


@router.get("/{work_order_id}", response_model=WorkOrderResponse)
async def get_work_order(
    work_order_id: str,
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(WorkOrder).where(WorkOrder.id == work_order_id))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    return WorkOrderResponse.model_validate(wo)


@router.post("/{work_order_id}/close", response_model=WorkOrderResponse)
async def close_work_order(
    work_order_id: str,
    req: WorkOrderCloseRequest,
    principal: dict = Depends(require_role("operator")),
    db: AsyncSession = Depends(get_db),
):
    """Close with a resolution note — feeds MTTR and LOOP-01 labels."""
    result = await db.execute(select(WorkOrder).where(WorkOrder.id == work_order_id))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    if wo.status == WorkOrderStatus.done:
        return WorkOrderResponse.model_validate(wo)  # idempotent
    wo.status = WorkOrderStatus.done
    wo.resolution = req.resolution
    wo.parts_json = json.dumps(req.parts_used or [])
    if req.cost is not None:
        wo.cost_estimate = req.cost
    wo.closed_at = utcnow()
    await db.commit()
    await db.refresh(wo)
    if wo.created_at:
        workorder_close_latency_seconds.observe((wo.closed_at - wo.created_at).total_seconds())
    workorders_total.labels(status="done").inc()
    return WorkOrderResponse.model_validate(wo)
