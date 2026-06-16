import json
import uuid
import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from app.database import async_session_factory
from app.config import settings
from app.utils import utcnow
from app.aegis.models import Remediation
from app.aegis.schemas import RemediationSignal, IngestRequest
from app.aegis.rules import RuleRegistry, build_default_registry
from app.aegis.actions import ACTION_REGISTRY, RemediationAction, RemediationResult
from app.aegis.metrics import (
    aegis_scrape_duration,
    aegis_signals_total,
    aegis_decisions_total,
    aegis_remediations_total,
    aegis_remediation_duration,
    aegis_dlq_depth,
    aegis_active_remediations,
)
from app.aegis.config import AEGIS_DEFAULT_ACTION_TIMEOUT

logger = logging.getLogger(__name__)


class AegisEngine:
    def __init__(self, registry: Optional[RuleRegistry] = None,
                 scrape_interval: int = 15):
        self.registry = registry or build_default_registry()
        self.scrape_interval = scrape_interval
        self._stop = False
        self._backend_url = getattr(settings, 'aegis_backend_url', "http://localhost:8000")
        self._signal_history: dict[str, list[float]] = {}

    def stop(self):
        self._stop = True

    async def run_forever(self):
        logger.info("Aegis engine started (scrape interval=%ss)", self.scrape_interval)
        while not self._stop:
            start = time.time()
            try:
                async with async_session_factory() as db:
                    await self.run_cycle(db)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Aegis cycle failed")
            elapsed = time.time() - start
            sleep_time = max(0.5, self.scrape_interval - elapsed)
            await asyncio.sleep(sleep_time)
        logger.info("Aegis engine stopped")

    async def run_cycle(self, db):
        start = time.time()

        metrics_text = await self._scrape_metrics()
        if not metrics_text:
            logger.warning("Aegis scrape returned no metrics")
            return

        signals = self._classify_metrics(metrics_text)
        if not signals:
            return

        for signal in signals:
            rule = self.registry.get_matching_rule(signal)
            if rule:
                await self._execute_remediation(db, signal, rule)
            else:
                await self._escalate_human(db, signal)

        aegis_scrape_duration.observe(time.time() - start)

    async def _scrape_metrics(self) -> str:
        try:
            url = f"{self._backend_url}/metrics/"
            resp = await asyncio.to_thread(requests.get, url, timeout=10)
            if resp.status_code == 200:
                return resp.text
            url = f"{self._backend_url}/metrics"
            resp = await asyncio.to_thread(requests.get, url, timeout=10)
            if resp.status_code == 200:
                return resp.text
            logger.warning("Scrape returned status %d", resp.status_code)
            return ""
        except requests.RequestException as e:
            logger.warning("Scrape failed: %s", e)
            return ""

    def _classify_metrics(self, metrics_text: str) -> list[RemediationSignal]:
        signals: list[RemediationSignal] = []
        lines = metrics_text.splitlines()

        parsed: dict[str, float] = {}
        histogram_buckets: dict[str, dict[str, float]] = {}

        for line in lines:
            if not line.startswith("fleet_") or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            raw_name = parts[0]
            value_str = parts[-1]
            try:
                value = float(value_str)
            except ValueError:
                continue
            name = raw_name.split("{")[0]
            if "_bucket" in raw_name or "_sum" in raw_name or "_count" in raw_name:
                hist_key = name.replace("_bucket", "").replace("_sum", "").replace("_count", "")
                hist_base = name
                histogram_buckets.setdefault(hist_key, {})[hist_base] = value
            else:
                parsed[name] = value

        ts = utcnow()
        ts_iso = ts.isoformat()
        device_ids: list[str] = []

        active = parsed.get("fleet_active_devices")
        if active is not None:
            threshold = getattr(settings, 'aegis_active_devices_threshold', 2)
            if active <= threshold:
                severity = "critical" if active <= threshold * 0.5 else "warning"
                sig_id = str(uuid.uuid4())
                signal = RemediationSignal(
                    id=sig_id,
                    metric_name="fleet_active_devices",
                    value=active,
                    threshold=threshold,
                    severity=severity,
                    timestamp=ts,
                    device_ids=[],
                    window_seconds=60,
                    metadata={"source": "scrape", "type": "gauge"},
                )
                signals.append(signal)
                aegis_signals_total.labels(severity=severity, metric="fleet_active_devices").inc()
                self._signal_history.setdefault(sig_id, []).append(active)

        ota = parsed.get("fleet_ota_in_progress")
        if ota is not None:
            threshold = getattr(settings, 'aegis_ota_in_progress_threshold', 3)
            if ota > threshold:
                severity = "critical" if ota > threshold * 2 else "warning"
                sig_id = str(uuid.uuid4())
                signal = RemediationSignal(
                    id=sig_id,
                    metric_name="fleet_ota_in_progress",
                    value=ota,
                    threshold=threshold,
                    severity=severity,
                    timestamp=ts,
                    device_ids=[],
                    window_seconds=120,
                    metadata={"source": "scrape", "type": "gauge"},
                )
                signals.append(signal)
                aegis_signals_total.labels(severity=severity, metric="fleet_ota_in_progress").inc()

        for hist_key, buckets in histogram_buckets.items():
            sum_val = buckets.get(f"{hist_key}_sum", 0)
            count_val = buckets.get(f"{hist_key}_count", 0)
            if count_val > 0 and "latency" in hist_key:
                avg_latency = sum_val / count_val
                threshold = getattr(settings, 'aegis_latency_threshold', 0.5)
                if avg_latency > threshold:
                    severity = "critical" if avg_latency > threshold * 2 else "warning"
                    sig_id = str(uuid.uuid4())
                    signal = RemediationSignal(
                        id=sig_id,
                        metric_name=hist_key,
                        value=round(avg_latency, 4),
                        threshold=threshold,
                        severity=severity,
                        timestamp=ts,
                        device_ids=[],
                        window_seconds=60,
                        metadata={"source": "scrape", "type": "histogram", "count": count_val},
                    )
                    signals.append(signal)
                    aegis_signals_total.labels(severity=severity, metric=hist_key).inc()

        return signals

    async def _execute_remediation(self, db, signal: RemediationSignal, rule) -> dict:
        action = rule.get_action()
        if not action:
            return {"error": f"No action found for rule '{rule.name}'"}

        input_snapshot = signal.model_dump_json()
        device_ids_str = ",".join(signal.device_ids)

        remediation = Remediation(
            signal_id=signal.id,
            metric_name=signal.metric_name,
            value=signal.value,
            threshold=signal.threshold,
            severity=signal.severity,
            rule_name=rule.name,
            action_name=action.name,
            status="in_progress",
            input_snapshot=input_snapshot,
            device_ids=device_ids_str,
            started_at=utcnow(),
        )
        db.add(remediation)
        await db.commit()
        await db.refresh(remediation)
        aegis_active_remediations.inc()

        context = {}
        try:
            result = await asyncio.wait_for(
                action.execute_with_retry(signal, context),
                timeout=AEGIS_DEFAULT_ACTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            result = RemediationResult(
                success=False,
                error_message=f"Action '{action.name}' timed out after {AEGIS_DEFAULT_ACTION_TIMEOUT}s",
                output_snapshot={"timeout": True, "action": action.name},
            )

        output_snapshot_str = json.dumps(result.output_snapshot)
        now = utcnow()
        duration_ms = result.duration_ms
        if remediation.started_at:
            duration_ms = int((now - remediation.started_at).total_seconds() * 1000)

        status = "success" if result.success else "failed"
        if result.output_snapshot.get("dlq"):
            status = "dlq"
            aegis_dlq_depth.inc()

        remediation.status = status
        remediation.output_snapshot = output_snapshot_str
        remediation.error_message = result.error_message
        remediation.completed_at = now
        remediation.duration_ms = duration_ms
        await db.commit()
        aegis_active_remediations.dec()
        aegis_remediations_total.labels(action=action.name, status=status).inc()

        logger.info(
            "Remediation %s: rule=%s action=%s status=%s duration=%dms",
            remediation.id[:8], rule.name, action.name, status, duration_ms,
        )

        return {
            "remediation_id": remediation.id,
            "rule": rule.name,
            "action": action.name,
            "status": status,
            "duration_ms": duration_ms,
        }

    async def _escalate_human(self, db, signal: RemediationSignal):
        from app.alert_engine import AlertEngine
        engine = AlertEngine(db)
        anomalies = [{
            "type": "aegis_escalation",
            "severity": "critical",
            "message": (
                f"Aegis: No auto-remediation found for signal "
                f"metric={signal.metric_name} value={signal.value} "
                f"threshold={signal.threshold} severity={signal.severity}"
            ),
            "affected_device_ids": signal.device_ids,
            "timestamp": signal.timestamp.isoformat(),
        }]
        await engine.process_anomalies(anomalies)

        input_snapshot = signal.model_dump_json()
        remediation = Remediation(
            signal_id=signal.id,
            metric_name=signal.metric_name,
            value=signal.value,
            threshold=signal.threshold,
            severity="critical",
            rule_name="human_escalation",
            action_name="human_escalation",
            status="escalated",
            input_snapshot=input_snapshot,
            output_snapshot=json.dumps({
                "action": "human_escalation",
                "anomaly_type": "aegis_escalation",
            }),
            device_ids=",".join(signal.device_ids),
            started_at=utcnow(),
            completed_at=utcnow(),
        )
        db.add(remediation)
        await db.commit()

        aegis_decisions_total.labels(rule="human_escalation", decision="escalated").inc()
        aegis_remediations_total.labels(action="human_escalation", status="escalated").inc()
        logger.critical("Signal %s escalated to human: %s/%s", signal.id[:8], signal.metric_name, signal.value)

    async def process_ingest(self, db, req: IngestRequest) -> RemediationSignal:
        ts = utcnow()
        signal = RemediationSignal(
            id=str(uuid.uuid4()),
            metric_name=req.metric_name,
            value=req.value,
            threshold=req.threshold,
            severity=req.severity,
            timestamp=ts,
            device_ids=req.device_ids,
            window_seconds=req.window_seconds,
            metadata=req.metadata,
        )
        aegis_signals_total.labels(severity=req.severity, metric=req.metric_name).inc()

        rule = self.registry.get_matching_rule(signal)
        if rule:
            await self._execute_remediation(db, signal, rule)
        else:
            await self._escalate_human(db, signal)

        return signal
