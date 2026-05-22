import os
import asyncio
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
from sqlalchemy import select

from app.config import settings
from app.database import init_db, async_session_factory
from app.mqtt_client import mqtt_client
from app.routers import devices, ota, dashboard
from agents.routers import router as agents_router
from app.ota_manager import OtaStateMachine
from app.metrics import metrics_middleware, active_devices, total_devices, mqtt_messages_received
from app.models import Device, DeviceStatus

logging.basicConfig(level=getattr(logging, settings.log_level.upper()))
logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def handle_mqtt_register(payload: dict):
    async with async_session_factory() as db:
        name = payload.get("name", "unknown")
        result = await db.execute(select(Device).where(Device.name == name))
        existing = result.scalar_one_or_none()
        if existing:
            was_offline = existing.status == DeviceStatus.offline
            existing.status = DeviceStatus.online
            existing.last_seen = _utcnow()
            if was_offline:
                active_devices.inc()
        else:
            device = Device(
                name=name,
                firmware_version=payload.get("firmware_version", "1.0.0"),
                status=DeviceStatus.online,
                last_seen=_utcnow(),
                ip_address=payload.get("ip_address", ""),
            )
            db.add(device)
            active_devices.inc()
            total_devices.inc()
            logger.info(f"MQTT auto-registered device: {name}")
        await db.commit()
    mqtt_messages_received.labels(topic="register").inc()


async def handle_mqtt_heartbeat(device_id: str, payload: dict):
    async with async_session_factory() as db:
        result = await db.execute(select(Device).where(Device.id == device_id))
        device = result.scalar_one_or_none()
        if device:
            device.last_seen = _utcnow()
            device.uptime_percentage = payload.get("uptime_percentage", 100.0)
            device.signal_strength = payload.get("signal_strength", 0)
            device.status = DeviceStatus.online
            await db.commit()
    mqtt_messages_received.labels(topic="heartbeat").inc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Fleet Commander backend...")
    await init_db()
    loop = asyncio.get_running_loop()
    mqtt_client.set_event_loop(loop)
    mqtt_client.on_ota_status(OtaStateMachine.handle_ota_status)
    mqtt_client.on_heartbeat(handle_mqtt_heartbeat)
    mqtt_client.on_register(handle_mqtt_register)
    mqtt_client.connect()
    yield
    mqtt_client.disconnect()
    logger.info("Fleet Commander backend shut down.")


app = FastAPI(
    title="Fleet Commander",
    description="Production-grade IoT device management module",
    version="1.0.0",
    lifespan=lifespan,
)

app.middleware("http")(metrics_middleware)

app.include_router(devices.router)
app.include_router(ota.router)
app.include_router(dashboard.router)
app.include_router(agents_router)

os.makedirs(settings.firmware_storage_path, exist_ok=True)


@app.get("/firmware/{filename}")
async def serve_firmware(filename: str):
    file_path = os.path.join(settings.firmware_storage_path, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Firmware file not found")
    return FileResponse(file_path, media_type="application/octet-stream", filename=filename)


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
