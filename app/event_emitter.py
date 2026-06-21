"""
Fleet Commander — Event Emitter (Feature 11)

Emits outbound events to registered webhook subscriptions.
Supports HMAC-signed deliveries and retry tracking.
"""

from __future__ import annotations

import json
import hmac
import hashlib
import logging
import asyncio
from typing import Optional

import requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WebhookSubscription, EventLog
from app.utils import utcnow
from app.metrics import events_emitted_total, webhook_deliveries_total

logger = logging.getLogger(__name__)


async def emit_event(
    db: AsyncSession,
    event_type: str,
    payload: dict,
) -> EventLog:
    """Record an event and fan out to matching webhook subscriptions."""
    entry = EventLog(
        event_type=event_type,
        payload=json.dumps(payload),
        timestamp=utcnow(),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    events_emitted_total.labels(event_type=event_type).inc()

    # Fan out to matching webhooks in a background thread
    result = await db.execute(select(WebhookSubscription).where(WebhookSubscription.enabled == True))
    subs = result.scalars().all()

    for sub in subs:
        if sub.event_types != "*" and event_type not in sub.event_types.split(","):
            continue
        asyncio.create_task(_deliver_webhook(sub, entry.id, event_type, payload, db))

    return entry


async def _deliver_webhook(
    sub: WebhookSubscription,
    event_id: str,
    event_type: str,
    payload: dict,
    db: AsyncSession,
):
    """Deliver a single webhook with HMAC signing."""
    body = json.dumps({
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
        "timestamp": utcnow().isoformat(),
    })
    headers = {"Content-Type": "application/json"}
    if sub.secret:
        signature = hmac.new(
            sub.secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        headers["X-Fleet-Signature"] = f"sha256={signature}"

    def _post():
        try:
            resp = requests.post(sub.url, data=body, headers=headers, timeout=10)
            return resp.status_code in (200, 201, 202, 204)
        except Exception:
            return False

    ok = await asyncio.to_thread(_post)
    webhook_deliveries_total.labels(result="success" if ok else "failed").inc()

    # Update event delivery counts
    try:
        from app.database import async_session_factory
        async with async_session_factory() as sess:
            entry = await sess.get(EventLog, event_id)
            if entry:
                if ok:
                    entry.delivered += 1
                else:
                    entry.failed += 1
                await sess.commit()
    except Exception:
        logger.debug("Could not update event delivery counts")


async def get_events(
    db: AsyncSession,
    event_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Fetch paginated event log."""
    from sqlalchemy import func
    query = select(EventLog)
    count_query = select(func.count()).select_from(EventLog)
    if event_type:
        query = query.where(EventLog.event_type == event_type)
        count_query = count_query.where(EventLog.event_type == event_type)
    query = query.order_by(EventLog.timestamp.desc())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    result = await db.execute(query.offset(offset).limit(limit))
    events = result.scalars().all()
    return {"events": events, "total": total}
