import json
import time
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from app.config import settings
from app.mqtt_client import mqtt_client
from app.utils import utcnow
from app.aegis.schemas import RemediationSignal
from app.aegis.config import (
    AEGIS_OTA_THROTTLE_DURATION_SECONDS,
    AEGIS_HEARTBEAT_FAST_INTERVAL,
    AEGIS_HEARTBEAT_NORMAL_INTERVAL,
)
from app.aegis.metrics import aegis_remediations_total, aegis_remediation_duration

logger = logging.getLogger(__name__)


class RemediationResult:
    def __init__(self, success: bool = True, output_snapshot: Optional[dict] = None,
                 error_message: Optional[str] = None, duration_ms: int = 0):
        self.success = success
        self.output_snapshot = output_snapshot or {}
        self.error_message = error_message
        self.duration_ms = duration_ms


class RemediationAction(ABC):
    name: str = ""
    timeout: int = 30
    max_retries: int = 3

    @abstractmethod
    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        ...

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        return True

    async def execute_with_retry(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                start = time.time()
                result = await self.execute(signal, context)
                elapsed_ms = int((time.time() - start) * 1000)
                result.duration_ms = elapsed_ms
                aegis_remediation_duration.labels(action=self.name).observe(elapsed_ms / 1000.0)
                status = "success" if result.success else "failed"
                aegis_remediations_total.labels(action=self.name, status=status).inc()
                return result
            except Exception as e:
                last_error = str(e)
                logger.warning("Action %s attempt %d failed: %s", self.name, attempt + 1, last_error)
                aegis_remediations_total.labels(action=self.name, status="failed").inc()
                if attempt < self.max_retries:
                    backoff = 2 ** attempt
                    await asyncio.sleep(backoff)
        return RemediationResult(
            success=False,
            error_message=f"Exhausted {self.max_retries} retries: {last_error}",
            output_snapshot={"dlq": True, "action": self.name},
        )


class ThrottleOtaAction(RemediationAction):
    name = "throttle_ota"
    timeout = 15

    def __init__(self):
        self._throttled = False

    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        self._throttled = True
        context["ota_throttled"] = True
        return RemediationResult(
            success=True,
            output_snapshot={
                "action": "throttle_ota",
                "throttled": True,
                "duration_seconds": AEGIS_OTA_THROTTLE_DURATION_SECONDS,
                "reason": f"ota_in_progress={signal.value} > threshold={signal.threshold}",
            },
        )

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        self._throttled = False
        context["ota_throttled"] = False
        if mqtt_client.is_connected:
            payload = json.dumps({
                "command": "resume_ota",
                "reason": "rollback",
                "timestamp": utcnow().isoformat(),
            })
            mqtt_client.client.publish("iot/fleet/command/ota_resume", payload, qos=1)
        logger.info("OTA throttle released")
        return True


class MqttQosDowngradeAction(RemediationAction):
    name = "mqtt_qos_downgrade"
    timeout = 10

    NON_CRITICAL_TOPICS = [
        "iot/fleet/+/heartbeat",
        "iot/fleet/+/status/ota",
    ]

    def __init__(self):
        self._downgraded_topics: list[str] = []

    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        self._downgraded_topics = list(self.NON_CRITICAL_TOPICS)
        return RemediationResult(
            success=True,
            output_snapshot={
                "action": "mqtt_qos_downgrade",
                "downgraded_topics": self._downgraded_topics,
                "from_qos": 1,
                "to_qos": 0,
            },
        )

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        topics = list(self._downgraded_topics)
        self._downgraded_topics = []
        if mqtt_client.is_connected:
            payload = json.dumps({
                "command": "restore_qos",
                "topics": topics,
                "timestamp": utcnow().isoformat(),
            })
            mqtt_client.client.publish("iot/fleet/command/qos_restore", payload, qos=1)
        logger.info("MQTT QoS restored for %d topics", len(topics))
        return True


class DeviceSoftRestartAction(RemediationAction):
    name = "device_soft_restart"
    timeout = 30

    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        device_ids = signal.device_ids
        published = []
        failed = []
        for device_id in device_ids:
            if mqtt_client.is_connected:
                topic = f"iot/fleet/{device_id}/command/restart"
                payload = json.dumps({
                    "command": "restart",
                    "reason": "signal_degraded",
                    "timestamp": utcnow().isoformat(),
                })
                result = mqtt_client.client.publish(topic, payload, qos=1)
                if result.rc == 0:
                    published.append(device_id)
                else:
                    failed.append(device_id)
            else:
                failed.append(device_id)
        success = len(failed) == 0
        return RemediationResult(
            success=success,
            output_snapshot={
                "action": "device_soft_restart",
                "devices_published": published,
                "devices_failed": failed,
                "reason": f"signal < -90dBm for {len(device_ids)} devices",
            },
            error_message=f"MQTT publish failed for {len(failed)} devices" if failed else None,
        )

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        for device_id in signal.device_ids:
            if mqtt_client.is_connected:
                topic = f"iot/fleet/{device_id}/command/cancel_restart"
                payload = json.dumps({
                    "command": "cancel_restart",
                    "reason": "rollback",
                    "timestamp": utcnow().isoformat(),
                })
                mqtt_client.client.publish(topic, payload, qos=1)
        logger.info("Published rollback cancel_restart for %d devices", len(signal.device_ids))
        return True


class ScaleHeartbeatAction(RemediationAction):
    name = "scale_heartbeat"
    timeout = 15

    def __init__(self):
        self._original_intervals: dict[str, int] = {}

    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        device_ids = signal.device_ids
        configured = []
        for device_id in device_ids:
            self._original_intervals[device_id] = AEGIS_HEARTBEAT_NORMAL_INTERVAL
            if mqtt_client.is_connected:
                config = {"heartbeat_interval_seconds": AEGIS_HEARTBEAT_FAST_INTERVAL}
                topic = f"iot/fleet/{device_id}/command/config"
                payload = json.dumps({
                    "config": config,
                    "timestamp": utcnow().isoformat(),
                })
                mqtt_client.client.publish(topic, payload, qos=1)
                configured.append(device_id)
        return RemediationResult(
            success=True,
            output_snapshot={
                "action": "scale_heartbeat",
                "new_interval_seconds": AEGIS_HEARTBEAT_FAST_INTERVAL,
                "devices_configured": configured,
                "reason": "offline ratio exceeded threshold - increasing heartbeat frequency",
            },
        )

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        for device_id, interval in self._original_intervals.items():
            if mqtt_client.is_connected:
                config = {"heartbeat_interval_seconds": interval}
                topic = f"iot/fleet/{device_id}/command/config"
                payload = json.dumps({
                    "config": config,
                    "timestamp": utcnow().isoformat(),
                })
                mqtt_client.client.publish(topic, payload, qos=1)
        self._original_intervals.clear()
        logger.info("Heartbeat intervals restored to normal")
        return True


ACTION_REGISTRY: dict[str, RemediationAction] = {
    "throttle_ota": ThrottleOtaAction(),
    "mqtt_qos_downgrade": MqttQosDowngradeAction(),
    "device_soft_restart": DeviceSoftRestartAction(),
    "scale_heartbeat": ScaleHeartbeatAction(),
}
