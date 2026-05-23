import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import Device, OtaDeployment, OtaStatus, Firmware
from app.mqtt_client import mqtt_client
from app.config import settings

logger = logging.getLogger(__name__)


class OtaStateMachine:
    """
    OTA State Machine:
      pending -> downloading -> applying -> verifying -> success
                                           -> hash_mismatch -> rollback -> rolled_back
      pending -> failed (on timeout or max retries)

    On hash_mismatch:
      1. Backend logs the failure.
      2. Device simulator auto-reverts to previous firmware.
      3. Backend updates device firmware_version to previous_firmware_version.
      4. OTA deployment is marked as rolled_back.
    """

    STATE_TRANSITIONS = {
        OtaStatus.pending: [OtaStatus.downloading, OtaStatus.failed],
        OtaStatus.downloading: [OtaStatus.applying, OtaStatus.failed],
        OtaStatus.applying: [OtaStatus.verifying, OtaStatus.failed],
        OtaStatus.verifying: [OtaStatus.success, OtaStatus.hash_mismatch, OtaStatus.failed],
        OtaStatus.hash_mismatch: [OtaStatus.rollback],
        OtaStatus.rollback: [OtaStatus.rolled_back, OtaStatus.failed],
        OtaStatus.success: [],
        OtaStatus.rolled_back: [],
        OtaStatus.failed: [],
    }

    @staticmethod
    def can_transition(from_status: OtaStatus, to_status: OtaStatus) -> bool:
        return to_status in OtaStateMachine.STATE_TRANSITIONS.get(from_status, [])

    @staticmethod
    async def update_deployment_status(
        deployment_id: str, new_status: OtaStatus, error_message: Optional[str] = None
    ) -> Optional[OtaDeployment]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(OtaDeployment).where(OtaDeployment.id == deployment_id)
            )
            deployment = result.scalar_one_or_none()
            if not deployment:
                logger.error(f"OTA deployment {deployment_id} not found")
                return None

            if not OtaStateMachine.can_transition(deployment.status, new_status):
                logger.warning(
                    f"Invalid state transition: {deployment.status.value} -> {new_status.value} "
                    f"for deployment {deployment_id}"
                )
                return None

            deployment.status = new_status
            deployment.updated_at = _utcnow()
            if error_message:
                deployment.error_message = error_message

            if new_status in (OtaStatus.success, OtaStatus.rolled_back, OtaStatus.failed):
                device_result = await session.execute(
                    select(Device).where(Device.id == deployment.device_id)
                )
                device = device_result.scalar_one_or_none()
                if device:
                    if new_status == OtaStatus.success:
                        firmware_result = await session.execute(
                            select(Firmware).where(Firmware.id == deployment.firmware_id)
                        )
                        firmware = firmware_result.scalar_one_or_none()
                        if firmware:
                            device.previous_firmware_version = device.firmware_version
                            device.firmware_version = firmware.version
                            device.current_ota_id = None
                    elif new_status == OtaStatus.rolled_back:
                        if device.previous_firmware_version:
                            device.firmware_version = device.previous_firmware_version
                            device.previous_firmware_version = None
                        device.current_ota_id = None

            await session.commit()
            await session.refresh(deployment)
            logger.info(f"OTA deployment {deployment_id} -> {new_status.value}")
            return deployment

    @staticmethod
    async def handle_ota_status(device_id: str, payload: dict):
        status = payload.get("status", "")
        deployment_id = payload.get("deployment_id", "")

        status_map = {
            "downloading": OtaStatus.downloading,
            "applying": OtaStatus.applying,
            "verifying": OtaStatus.verifying,
            "success": OtaStatus.success,
            "hash_mismatch": OtaStatus.hash_mismatch,
            "rollback": OtaStatus.rollback,
            "rolled_back": OtaStatus.rolled_back,
            "failed": OtaStatus.failed,
        }

        new_status = status_map.get(status)
        if new_status is None:
            logger.warning(f"Unknown OTA status from device {device_id}: {status}")
            return

        if new_status == OtaStatus.hash_mismatch:
            error_msg = payload.get("error", "SHA256 hash mismatch")
            logger.warning(f"Device {device_id} reported hash mismatch: {error_msg}")
            await OtaStateMachine.update_deployment_status(deployment_id, OtaStatus.hash_mismatch, error_msg)
            await OtaStateMachine.update_deployment_status(deployment_id, OtaStatus.rollback)
            await OtaStateMachine.update_deployment_status(deployment_id, OtaStatus.rolled_back)
        elif new_status == OtaStatus.failed:
            error_msg = payload.get("error", "Unknown error")
            await OtaStateMachine.update_deployment_status(deployment_id, OtaStatus.failed, error_msg)
        else:
            await OtaStateMachine.update_deployment_status(deployment_id, new_status)


class OtaTimeoutWatcher:
    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}

    async def watch_deployment(self, deployment_id: str, device_id: str):
        await asyncio.sleep(settings.ota_timeout_seconds)
        async with async_session_factory() as session:
            result = await session.execute(
                select(OtaDeployment).where(OtaDeployment.id == deployment_id)
            )
            deployment = result.scalar_one_or_none()
            if deployment and deployment.status not in (
                OtaStatus.success, OtaStatus.rolled_back, OtaStatus.failed, OtaStatus.hash_mismatch
            ):
                logger.warning(f"OTA deployment {deployment_id} timed out for device {device_id}")
                if deployment.retry_count < settings.max_retry_count:
                    deployment.retry_count += 1
                    deployment.status = OtaStatus.pending
                    await session.commit()

                    firmware_url = deployment.firmware_url
                    sha256 = ""
                    if firmware_url:
                        # Extract hash from firmware record if available
                        fw_result = await session.execute(
                            select(Firmware).where(Firmware.id == deployment.firmware_id)
                        )
                        fw = fw_result.scalar_one_or_none()
                        if fw:
                            sha256 = fw.sha256_hash
                    else:
                        # Fallback: rebuild from firmware record (legacy deployments)
                        fw_result = await session.execute(
                            select(Firmware).where(Firmware.id == deployment.firmware_id)
                        )
                        fw = fw_result.scalar_one_or_none()
                        if fw:
                            firmware_url = f"{settings.ota_firmware_base_url}/firmware/{fw.filename}"
                            sha256 = fw.sha256_hash

                    if firmware_url:
                        success = mqtt_client.publish_ota_command(
                            device_id,
                            firmware_url,
                            sha256,
                            deployment_id,
                        )
                        if success:
                            async with async_session_factory() as retry_session:
                                dep = await retry_session.get(OtaDeployment, deployment_id)
                                if dep:
                                    dep.status = OtaStatus.downloading
                                    await retry_session.commit()
                            # Restart the watch for this retry attempt
                            self.start_watch(deployment_id, device_id)
                else:
                    await OtaStateMachine.update_deployment_status(
                        deployment_id, OtaStatus.failed, "Timeout after max retries"
                    )

    def start_watch(self, deployment_id: str, device_id: str):
        if deployment_id in self._tasks:
            self._tasks[deployment_id].cancel()
        self._tasks[deployment_id] = asyncio.create_task(
            self.watch_deployment(deployment_id, device_id)
        )

    def cancel_watch(self, deployment_id: str):
        if deployment_id in self._tasks:
            self._tasks[deployment_id].cancel()
            del self._tasks[deployment_id]


ota_timeout_watcher = OtaTimeoutWatcher()
