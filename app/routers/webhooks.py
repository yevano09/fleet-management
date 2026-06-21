"""
Fleet Commander — Webhook & Event Stream API (Feature 11)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import WebhookSubscription, EventLog
from app.schemas import WebhookCreateRequest, WebhookResponse, EventLogResponse
from app.audit import log_action
from app.event_emitter import get_events

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc()))
    return [WebhookResponse.model_validate(w) for w in result.scalars().all()]


@router.post("", response_model=WebhookResponse, status_code=201)
async def create_webhook(req: WebhookCreateRequest, db: AsyncSession = Depends(get_db)):
    sub = WebhookSubscription(
        name=req.name, url=req.url, event_types=req.event_types,
        secret=req.secret, enabled=req.enabled,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    await log_action(db, "dashboard", "webhook.create", "webhook", sub.id, {"name": req.name, "url": req.url})
    return WebhookResponse.model_validate(sub)


@router.delete("/{webhook_id}")
async def delete_webhook(webhook_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WebhookSubscription).where(WebhookSubscription.id == webhook_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(sub)
    await db.commit()
    return {"message": "Webhook deleted", "webhook_id": webhook_id}


@router.patch("/{webhook_id}/toggle")
async def toggle_webhook(webhook_id: str, enabled: bool = True, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WebhookSubscription).where(WebhookSubscription.id == webhook_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found")
    sub.enabled = enabled
    await db.commit()
    return {"message": f"Webhook {'enabled' if enabled else 'disabled'}"}


@router.get("/events", response_model=list[EventLogResponse])
async def list_events(
    event_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    result = await get_events(db, event_type=event_type, limit=limit, offset=offset)
    return [EventLogResponse.model_validate(e) for e in result["events"]]


@router.post("/test/{webhook_id}")
async def test_webhook(webhook_id: str, db: AsyncSession = Depends(get_db)):
    """Send a test event to a webhook subscription."""
    result = await db.execute(select(WebhookSubscription).where(WebhookSubscription.id == webhook_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook not found")
    from app.event_emitter import emit_event
    await emit_event(db, "webhook.test", {"webhook_id": webhook_id, "message": "Test delivery"})
    return {"message": "Test event emitted", "webhook_id": webhook_id}
