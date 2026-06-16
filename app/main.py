import os
import asyncio
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.responses import FileResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
from sqlalchemy import select

from app.config import settings, validate_settings
from app.database import init_db, async_session_factory
from app.mqtt_client import mqtt_client
from app.routers import devices, ota, dashboard, auth, admin
from app.routers.alerts import router as alerts_router
from agents.routers import router as agents_router
from app.aegis.router import router as aegis_router
from app.ota_manager import OtaStateMachine
from app.metrics import metrics_middleware, active_devices, total_devices, mqtt_messages_received, v2g_active_discharges, device_soc
from app.models import Device, DeviceStatus
from app.utils import utcnow

logging.basicConfig(level=getattr(logging, settings.log_level.upper()))
logger = logging.getLogger(__name__)


async def handle_mqtt_register(payload: dict):
    async with async_session_factory() as db:
        name = payload.get("name", "unknown")
        device_id = payload.get("device_id")
        mqtt_id = payload.get("device_id")  # the MQTT client's identifier (MAC for ESP32)
        # Look up by mqtt_client_id first, then by device.id, then by name
        existing = None
        if mqtt_id:
            result = await db.execute(select(Device).where(Device.mqtt_client_id == mqtt_id))
            existing = result.scalar_one_or_none()
        if not existing and device_id:
            result = await db.execute(select(Device).where(Device.id == device_id))
            existing = result.scalar_one_or_none()
        if not existing:
            result = await db.execute(select(Device).where(Device.name == name))
            existing = result.scalar_one_or_none()
        if existing:
            was_offline = existing.status == DeviceStatus.offline
            existing.status = DeviceStatus.online
            existing.last_seen = utcnow()
            existing.name = name
            existing.mqtt_client_id = mqtt_id or existing.mqtt_client_id
            existing.ip_address = payload.get("ip_address", existing.ip_address)
            existing.firmware_version = payload.get("firmware_version", existing.firmware_version)
            if was_offline:
                active_devices.inc()
        else:
            device = Device(
                id=device_id,
                name=name,
                mqtt_client_id=mqtt_id,
                firmware_version=payload.get("firmware_version", "1.0.0"),
                status=DeviceStatus.online,
                last_seen=utcnow(),
                ip_address=payload.get("ip_address", ""),
            )
            db.add(device)
            active_devices.inc()
            total_devices.inc()
            logger.info("MQTT auto-registered device: %s (id=%s, mqtt_id=%s)", name, device_id, mqtt_id)
        await db.commit()
    mqtt_messages_received.labels(topic="register").inc()


async def handle_mqtt_heartbeat(device_id: str, payload: dict):
    async with async_session_factory() as db:
        result = await db.execute(select(Device).where(Device.id == device_id))
        device = result.scalar_one_or_none()
        if device:
            device.last_seen = utcnow()
            device.uptime_percentage = payload.get("uptime_percentage", 100.0)
            device.signal_strength = payload.get("signal_strength", 0)
            device.status = DeviceStatus.online
            if "soc" in payload:
                device.soc = float(payload["soc"])
                device_soc.labels(device=device.name).set(float(payload["soc"]))
            if "soh" in payload:
                device.soh = float(payload["soh"])
            if "battery_temp" in payload:
                device.battery_temp = float(payload["battery_temp"])
            if "plug_status" in payload:
                old_plug = device.plug_status
                device.plug_status = payload["plug_status"]
                if payload["plug_status"] == "discharging" and old_plug != "discharging":
                    v2g_active_discharges.inc()
                elif old_plug == "discharging" and payload["plug_status"] != "discharging":
                    v2g_active_discharges.dec()
            await db.commit()
    mqtt_messages_received.labels(topic="heartbeat").inc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Fleet Commander backend...")
    validate_settings()
    os.makedirs(settings.firmware_storage_path, exist_ok=True)
    await init_db()
    loop = asyncio.get_running_loop()
    mqtt_client.set_event_loop(loop)
    mqtt_client.on_ota_status(OtaStateMachine.handle_ota_status)
    mqtt_client.on_heartbeat(handle_mqtt_heartbeat)
    mqtt_client.on_register(handle_mqtt_register)
    mqtt_client.connect()

    from app.aegis.engine import AegisEngine
    aegis_engine = AegisEngine(scrape_interval=settings.aegis_scrape_interval)
    aegis_task = asyncio.create_task(aegis_engine.run_forever())
    logger.info("Aegis auto-remediation engine started (interval=%ss)", settings.aegis_scrape_interval)

    logger.info("Fleet Commander backend started.")
    yield
    aegis_engine.stop()
    aegis_task.cancel()
    try:
        await aegis_task
    except asyncio.CancelledError:
        pass
    mqtt_client.disconnect()
    logger.info("Fleet Commander backend shut down.")


app = FastAPI(
    title="Fleet Commander",
    description="Production-grade IoT device management module",
    version="1.0.0",
    lifespan=lifespan,
)

app.middleware("http")(metrics_middleware)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(devices.router)
app.include_router(ota.router)
app.include_router(dashboard.router)
app.include_router(alerts_router)
app.include_router(agents_router)
app.include_router(aegis_router)


@app.get("/firmware/{filename}")
async def serve_firmware(filename: str):
    storage = os.path.realpath(settings.firmware_storage_path)
    file_path = os.path.realpath(os.path.join(storage, filename))
    if not file_path.startswith(storage + os.sep):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Firmware file not found")
    return FileResponse(file_path, media_type="application/octet-stream", filename=filename)


@app.get("/health/mqtt")
async def health_mqtt():
    return {"connected": mqtt_client._connected}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
