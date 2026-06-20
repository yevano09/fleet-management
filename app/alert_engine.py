"""
Fleet Commander — Alert Engine

Core alerting pipeline: deduplication, cooldown, persistence, notification.

Usage:
    from app.alert_engine import AlertEngine
    engine = AlertEngine(db)
    processed = await engine.process_anomalies(anomalies)
"""

from __future__ import annotations

import logging
import time
import os
import json
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, AlertStatus
from app.utils import utcnow
from app.config import settings
from app.metrics import alerts_total, alerts_active, alert_notifications_total

logger = logging.getLogger(__name__)


class AlertChannel:
    """Abstract base for notification channels."""

    async def send(self, alert: dict) -> bool:
        raise NotImplementedError


class SlackChannel(AlertChannel):
    """Send alerts to Slack via webhook."""

    async def send(self, alert: dict) -> bool:
        url = settings.slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")
        if not url:
            logger.info("[Slack disabled] %s: %s", alert.get("severity"), alert.get("message"))
            return False
        try:
            import requests
            color = {"info": "#36a64f", "warning": "#f59f00", "critical": "#f03e3e"}
            resp = requests.post(
                url,
                json={
                    "attachments": [{
                        "color": color.get(alert.get("severity", "info"), "#36a64f"),
                        "title": f"Fleet Commander — {alert.get('severity', 'INFO').upper()}",
                        "text": alert.get("message", ""),
                        "fields": [
                            {"title": "Type", "value": alert.get("type", "unknown"), "short": True},
                            {"title": "Count", "value": str(alert.get("count", 1)), "short": True},
                            {"title": "Devices", "value": alert.get("device_ids", "") or "none", "short": False},
                        ],
                        "ts": datetime.now(timezone.utc).timestamp(),
                    }]
                },
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            logger.exception("Failed to send Slack alert")
            return False


class EmailChannel(AlertChannel):
    """Send alerts via SMTP."""

    async def send(self, alert: dict) -> bool:
        if not settings.smtp_host:
            logger.info("[Email disabled] %s: %s", alert.get("severity"), alert.get("message"))
            return False
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg["From"] = settings.alert_email_from
            msg["To"] = settings.alert_email_to or settings.alert_email_from
            msg["Subject"] = f"[Fleet Commander] {alert.get('severity', 'INFO').upper()}: {alert.get('type', 'unknown')}"

            body = f"""
Type: {alert.get('type', 'unknown')}
Severity: {alert.get('severity', 'info')}
Message: {alert.get('message', '')}
Devices: {alert.get('device_ids', '') or 'none'}
Count: {alert.get('count', 1)}
Time: {utcnow().isoformat()}
"""
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                if settings.smtp_username:
                    server.starttls()
                    server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(msg)
            return True
        except Exception:
            logger.exception("Failed to send email alert")
            return False


class WebhookChannel(AlertChannel):
    """Send alerts to a generic webhook URL."""

    async def send(self, alert: dict) -> bool:
        url = settings.alert_webhook_url or os.environ.get("ALERT_WEBHOOK_URL", "")
        if not url:
            logger.info("[Webhook disabled] %s: %s", alert.get("severity"), alert.get("message"))
            return False
        try:
            import requests
            resp = requests.post(
                url,
                json={
                    "source": "fleet-commander",
                    "alert_id": alert.get("id", ""),
                    "type": alert.get("type", "unknown"),
                    "severity": alert.get("severity", "info"),
                    "message": alert.get("message", ""),
                    "device_ids": alert.get("device_ids", ""),
                    "count": alert.get("count", 1),
                    "timestamp": utcnow().isoformat(),
                },
                timeout=10,
                headers={"Content-Type": "application/json"},
            )
            return resp.status_code in (200, 201, 202, 204)
        except Exception:
            logger.exception("Failed to send webhook alert")
            return False


class AlertEngine:
    """Alert processing pipeline: dedup, throttle, persist, notify."""

    # Cooldown per alert type (seconds)
    COOLDOWN_SECONDS = {
        "weak_signal": 300,
        "stuck_ota": 120,
        "ota_failure_spike": 600,
        "mass_offline": 300,
        "device_offline": 600,
        "v2g_revenue_drop": 3600,
    }

    # Escalation threshold: if count >= this, bump severity
    ESCALATION_THRESHOLD = 3

    def __init__(self, db: AsyncSession):
        self.db = db
        self._channels: list[AlertChannel] = []
        self._cooldowns: dict[str, float] = {}

        # Build channel list based on configuration
        if settings.slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL"):
            self._channels.append(SlackChannel())
        if settings.smtp_host:
            self._channels.append(EmailChannel())
        if settings.alert_webhook_url or os.environ.get("ALERT_WEBHOOK_URL"):
            self._channels.append(WebhookChannel())

        logger.info("AlertEngine initialized with %d channels", len(self._channels))

    def _make_dedup_key(self, anomaly: dict) -> str:
        """Create a dedup key from type + primary device_id."""
        alert_type = anomaly.get("type", "unknown")
        affected = anomaly.get("affected_device_ids", [])
        primary = affected[0] if affected else ""
        return f"{alert_type}:{primary}"

    def _is_in_cooldown(self, dedup_key: str, alert_type: str) -> bool:
        """Check if this alert type is still in cooldown."""
        cooldown = self.COOLDOWN_SECONDS.get(alert_type, 300)
        last = self._cooldowns.get(dedup_key)
        if last is None:
            return False
        elapsed = time.time() - last
        return elapsed < cooldown

    def _record_cooldown(self, dedup_key: str) -> None:
        self._cooldowns[dedup_key] = time.time()

    async def _find_existing_alert(self, dedup_key: str) -> Optional[Alert]:
        """Find an active alert with the same dedup key."""
        result = await self.db.execute(
            select(Alert).where(
                Alert.dedup_key == dedup_key,
                Alert.status.in_([AlertStatus.active, AlertStatus.acknowledged]),
            )
        )
        return result.scalar_one_or_none()

    async def _update_existing_alert(self, alert: Alert) -> None:
        """Increment count and updated_at for re-fired alert."""
        alert.count += 1
        alert.updated_at = utcnow()
        # Escalation: bump severity if count exceeds threshold
        if alert.count >= self.ESCALATION_THRESHOLD and alert.severity == "warning":
            alert.severity = "critical"
            alert.message = f"[ESCALATED] {alert.message}"
        await self.db.commit()
        await self.db.refresh(alert)

    async def _create_alert(self, anomaly: dict) -> Alert:
        """Create a new alert row from anomaly data."""
        affected = anomaly.get("affected_device_ids", [])
        device_ids = ",".join(affected) if affected else ""
        dedup_key = self._make_dedup_key(anomaly)
        severity = anomaly.get("severity", "info")
        alert_type = anomaly.get("type", "unknown")

        alert = Alert(
            type=alert_type,
            severity=severity,
            message=anomaly.get("message", ""),
            device_ids=device_ids,
            status=AlertStatus.active,
            dedup_key=dedup_key,
            count=1,
            channel=",".join([c.__class__.__name__ for c in self._channels]),
        )
        self.db.add(alert)
        await self.db.commit()
        await self.db.refresh(alert)

        # Prometheus metrics
        alerts_total.labels(severity=severity, type=alert_type).inc()
        alerts_active.inc()

        return alert

    async def _notify_channels(self, alert: Alert) -> None:
        """Fan out to all configured channels."""
        alert_dict = {
            "id": alert.id,
            "type": alert.type,
            "severity": alert.severity,
            "message": alert.message,
            "device_ids": alert.device_ids,
            "count": alert.count,
            "status": alert.status.value,
        }
        for channel in self._channels:
            try:
                ok = await channel.send(alert_dict)
                if ok:
                    alert_notifications_total.labels(channel=channel.__class__.__name__).inc()
            except Exception:
                logger.exception("Channel %s failed", channel.__class__.__name__)

    async def process_anomalies(self, anomalies: list[dict]) -> list[dict]:
        """Process anomalies through dedup, throttle, persist, notify.

        Returns a list of processed alerts with status and IDs.
        """
        processed = []
        for anomaly in anomalies:
            dedup_key = self._make_dedup_key(anomaly)
            alert_type = anomaly.get("type", "unknown")

            # Check cooldown
            if self._is_in_cooldown(dedup_key, alert_type):
                logger.debug("Alert in cooldown, skipping")
                continue

            # Find existing active alert
            existing = await self._find_existing_alert(dedup_key)
            if existing:
                # Re-fire: increment count, update
                await self._update_existing_alert(existing)
                processed.append({
                    "id": existing.id,
                    "type": existing.type,
                    "severity": existing.severity,
                    "message": existing.message,
                    "status": existing.status.value,
                    "count": existing.count,
                    "dedup_key": existing.dedup_key,
                    "action": "incremented",
                })
                # Only notify on re-fire if count is a multiple of escalation threshold
                if existing.count % self.ESCALATION_THRESHOLD == 0:
                    await self._notify_channels(existing)
                self._record_cooldown(dedup_key)
                continue

            # New alert
            alert = await self._create_alert(anomaly)
            processed.append({
                "id": alert.id,
                "type": alert.type,
                "severity": alert.severity,
                "message": alert.message,
                "status": alert.status.value,
                "count": alert.count,
                "dedup_key": alert.dedup_key,
                "action": "created",
            })

            # Notify
            if self._channels:
                await self._notify_channels(alert)
            self._record_cooldown(dedup_key)

        return processed

    async def acknowledge_alert(self, alert_id: str, user: str) -> bool:
        """Acknowledge an active alert."""
        result = await self.db.execute(
            update(Alert)
            .where(Alert.id == alert_id, Alert.status == AlertStatus.active)
            .values(
                status=AlertStatus.acknowledged,
                acknowledged_by=user,
                acknowledged_at=utcnow(),
            )
        )
        await self.db.commit()
        return result.rowcount > 0

    async def resolve_alert(self, alert_id: str) -> bool:
        """Resolve an alert."""
        result = await self.db.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(
                status=AlertStatus.resolved,
                resolved_at=utcnow(),
            )
        )
        await self.db.commit()
        return result.rowcount > 0

    async def get_active_alerts(self, severity: Optional[str] = None) -> list[dict]:
        """Fetch active alerts, optionally filtered by severity."""
        query = select(Alert).where(
            Alert.status.in_([AlertStatus.active, AlertStatus.acknowledged])
        )
        if severity:
            query = query.where(Alert.severity == severity)
        query = query.order_by(Alert.created_at.desc())
        result = await self.db.execute(query)
        alerts = result.scalars().all()
        return [{
            "id": a.id,
            "type": a.type,
            "severity": a.severity,
            "message": a.message,
            "device_ids": a.device_ids,
            "status": a.status.value,
            "count": a.count,
            "dedup_key": a.dedup_key,
            "acknowledged_by": a.acknowledged_by,
            "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        } for a in alerts]

    async def get_alert_history(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        alert_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Fetch paginated alert history."""
        query = select(Alert)
        if status:
            query = query.where(Alert.status == status)
        if severity:
            query = query.where(Alert.severity == severity)
        if alert_type:
            query = query.where(Alert.type == alert_type)
        query = query.order_by(Alert.created_at.desc())

        # Count total
        count_query = select(func.count()).select_from(Alert)
        if status:
            count_query = count_query.where(Alert.status == status)
        if severity:
            count_query = count_query.where(Alert.severity == severity)
        if alert_type:
            count_query = count_query.where(Alert.type == alert_type)

        total_result = await self.db.execute(count_query)
        total = total_result.scalar()

        result = await self.db.execute(query.offset(offset).limit(limit))
        alerts = result.scalars().all()
        return {
            "alerts": [{
                "id": a.id,
                "type": a.type,
                "severity": a.severity,
                "message": a.message,
                "device_ids": a.device_ids,
                "status": a.status.value,
                "count": a.count,
                "dedup_key": a.dedup_key,
                "acknowledged_by": a.acknowledged_by,
                "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            } for a in alerts],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def prune_old_alerts(self, days: int = 7) -> int:
        """Delete resolved alerts older than N days."""
        from datetime import timedelta
        cutoff = utcnow() - timedelta(days=days)
        result = await self.db.execute(
            delete(Alert).where(
                Alert.status == AlertStatus.resolved,
                Alert.resolved_at < cutoff,
            )
        )
        await self.db.commit()
        return result.rowcount
