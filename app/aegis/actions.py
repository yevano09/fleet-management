import json
import time
import os
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
from app.aegis.metrics import aegis_remediation_duration

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
                return result
            except Exception as e:
                last_error = str(e)
                logger.warning("Action %s attempt %d failed: %s", self.name, attempt + 1, last_error)
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


class RollbackOtaBatchAction(RemediationAction):
    name = "rollback_ota_batch"
    timeout = 30

    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        device_ids = signal.device_ids
        if not device_ids:
            return RemediationResult(
                success=True,
                output_snapshot={
                    "action": "rollback_ota_batch",
                    "devices_rolled_back": [],
                    "devices_failed": [],
                    "alerts_created": 0,
                    "note": "No device IDs provided",
                },
            )

        rolled_back = []
        failed = []

        from sqlalchemy import select
        from app.models import Device
        from app.alert_engine import AlertEngine

        db = context.get("db")
        if db is None:
            from app.database import async_session_factory
            async with async_session_factory() as session:
                return await self._do_rollback(session, device_ids)
        return await self._do_rollback(db, device_ids)

    async def _do_rollback(self, db, device_ids: list) -> RemediationResult:
        from sqlalchemy import select
        from app.models import Device
        from app.alert_engine import AlertEngine

        rolled_back = []
        failed = []

        for device_id in device_ids:
            result = await db.execute(select(Device).where(Device.id == device_id))
            device = result.scalar_one_or_none()
            if device and device.previous_firmware_version:
                device.firmware_version = device.previous_firmware_version
                rolled_back.append(device_id)
                if mqtt_client.is_connected:
                    topic = f"iot/fleet/{device_id}/command/rollback"
                    payload = json.dumps({
                        "command": "rollback",
                        "previous_firmware": device.previous_firmware_version,
                        "timestamp": utcnow().isoformat(),
                    })
                    mqtt_client.client.publish(topic, payload, qos=1)
            else:
                failed.append(device_id)

        await db.commit()

        if rolled_back:
            engine = AlertEngine(db)
            anomalies = [{
                "type": "ota_batch_rollback",
                "severity": "critical",
                "message": f"Rolled back OTA for {len(rolled_back)} devices due to failure spike > 30%",
                "affected_device_ids": rolled_back,
                "timestamp": utcnow().isoformat(),
            }]
            await engine.process_anomalies(anomalies)

        return RemediationResult(
            success=len(failed) == 0 or len(rolled_back) > 0,
            output_snapshot={
                "action": "rollback_ota_batch",
                "devices_rolled_back": rolled_back,
                "devices_failed": failed,
                "alerts_created": 1 if rolled_back else 0,
            },
            error_message=f"Failed to rollback {len(failed)} devices" if failed else None,
        )

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        return True

    async def status(self) -> dict:
        return {"action": self.name, "ready": True}


class HumanEscalationAction(RemediationAction):
    name = "human_escalation"
    timeout = 15

    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        from app.alert_engine import AlertEngine
        from app.database import async_session_factory

        db = context.get("db")
        if db is None:
            async with async_session_factory() as session:
                engine = AlertEngine(session)
                anomalies = [{
                    "type": "human_escalation",
                    "severity": "critical",
                    "message": (
                        f"Aegis: Auto-remediation exhausted for signal "
                        f"metric={signal.metric_name} value={signal.value} "
                        f"threshold={signal.threshold}"
                    ),
                    "affected_device_ids": signal.device_ids,
                    "timestamp": utcnow().isoformat(),
                }]
                await engine.process_anomalies(anomalies)
        else:
            engine = AlertEngine(db)
            anomalies = [{
                "type": "human_escalation",
                "severity": "critical",
                "message": (
                    f"Aegis: Auto-remediation exhausted for signal "
                    f"metric={signal.metric_name} value={signal.value} "
                    f"threshold={signal.threshold}"
                ),
                "affected_device_ids": signal.device_ids,
                "timestamp": utcnow().isoformat(),
            }]
            await engine.process_anomalies(anomalies)

        return RemediationResult(
            success=True,
            output_snapshot={
                "action": "human_escalation",
                "metric": signal.metric_name,
                "value": signal.value,
                "alert_type": "human_escalation",
                "severity": "critical",
                "auto_assigned": "on-call",
            },
        )

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        return True

    async def status(self) -> dict:
        return {"action": self.name, "ready": True}


class MigrateDevicePoolAction(RemediationAction):
    name = "migrate_device_pool"
    timeout = 20

    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        device_ids = signal.device_ids
        migrated = []
        failed = []

        for device_id in device_ids:
            if mqtt_client.is_connected:
                topic = f"iot/fleet/{device_id}/command/maintenance"
                payload = json.dumps({
                    "command": "enter_maintenance",
                    "reason": "resource_pressure",
                    "timestamp": utcnow().isoformat(),
                })
                result = mqtt_client.client.publish(topic, payload, qos=1)
                if result.rc == 0:
                    migrated.append(device_id)
                else:
                    failed.append(device_id)
            else:
                failed.append(device_id)

        return RemediationResult(
            success=len(failed) == 0,
            output_snapshot={
                "action": "migrate_device_pool",
                "devices_migrated": migrated,
                "devices_failed": failed,
                "mode": "maintenance",
            },
            error_message=f"Failed to migrate {len(failed)} devices" if failed else None,
        )

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        for device_id in signal.device_ids:
            if mqtt_client.is_connected:
                topic = f"iot/fleet/{device_id}/command/maintenance"
                payload = json.dumps({
                    "command": "exit_maintenance",
                    "reason": "rollback",
                    "timestamp": utcnow().isoformat(),
                })
                mqtt_client.client.publish(topic, payload, qos=1)
        return True

    async def status(self) -> dict:
        return {"action": self.name, "ready": True}


class CleanupFirmwareArtifactsAction(RemediationAction):
    name = "cleanup_firmware_artifacts"
    timeout = 30

    async def execute(self, signal: RemediationSignal, context: dict) -> RemediationResult:
        from sqlalchemy import select
        from app.models import OtaDeployment, OtaStatus

        artifacts_deleted = 0
        total_freed_bytes = 0
        storage_path = settings.firmware_storage_path
        storage_path = os.path.realpath(storage_path)

        db = context.get("db")
        if db is None:
            from app.database import async_session_factory
            async with async_session_factory() as session:
                return await self._do_cleanup(session, storage_path)
        return await self._do_cleanup(db, storage_path)

    async def _do_cleanup(self, db, storage_path: str) -> RemediationResult:
        from sqlalchemy import select
        from app.models import OtaDeployment, OtaStatus

        artifacts_deleted = 0
        total_freed_bytes = 0

        result = await db.execute(
            select(OtaDeployment).where(
                OtaDeployment.status.in_([OtaStatus.success, OtaStatus.failed, OtaStatus.rolled_back])
            ).order_by(OtaDeployment.updated_at.asc())
        )
        deployments = result.scalars().all()

        for dep in deployments:
                if total_freed_bytes > 10 * 1024 * 1024:
                    break
                if dep.firmware_url:
                    fpath = dep.firmware_url
                    if not os.path.isabs(fpath):
                        fpath = os.path.join(storage_path, fpath)
                    try:
                        if os.path.exists(fpath):
                            size = os.path.getsize(fpath)
                            os.remove(fpath)
                            artifacts_deleted += 1
                            total_freed_bytes += size
                    except (OSError, PermissionError):
                        pass

        freed_mb = round(total_freed_bytes / (1024 * 1024), 2)
        logger.info("Cleaned up %d firmware artifacts, freed %s MB", artifacts_deleted, freed_mb)

        return RemediationResult(
            success=True,
            output_snapshot={
                "action": "cleanup_firmware_artifacts",
                "artifacts_deleted": artifacts_deleted,
                "total_freed_bytes": total_freed_bytes,
                "total_freed_mb": freed_mb,
            },
        )

    async def rollback(self, signal: RemediationSignal, context: dict) -> bool:
        return True

    async def status(self) -> dict:
        return {"action": self.name, "ready": True}


ACTION_REGISTRY: dict[str, RemediationAction] = {
    "throttle_ota": ThrottleOtaAction(),
    "mqtt_qos_downgrade": MqttQosDowngradeAction(),
    "device_soft_restart": DeviceSoftRestartAction(),
    "scale_heartbeat": ScaleHeartbeatAction(),
    "rollback_ota_batch": RollbackOtaBatchAction(),
    "human_escalation": HumanEscalationAction(),
    "migrate_device_pool": MigrateDevicePoolAction(),
    "cleanup_firmware_artifacts": CleanupFirmwareArtifactsAction(),
}
