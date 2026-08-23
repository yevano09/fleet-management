import os
import json
import hashlib
import hmac
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.responses import FileResponse, HTMLResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
from sqlalchemy import select, update, delete, text

from app.config import settings, validate_settings, DEFAULT_ORG_ID
from app.database import init_db, async_session_factory
from app.mqtt_client import mqtt_client
from app.routers import devices, ota, dashboard, auth, admin
from app.routers.alerts import router as alerts_router
from app.routers.telemetry import router as telemetry_router
from app.routers.geofences import router as geofences_router
from app.routers.lifecycle import router as lifecycle_router
from app.routers.scheduled_ota import router as scheduled_ota_router
from app.routers.command_queue import router as command_queue_router
from app.routers.audit import router as audit_router
from app.routers.shadow import router as shadow_router
from app.routers.predictive import router as predictive_router
from app.routers.webhooks import router as webhooks_router
from app.routers.provisioning import router as provisioning_router
from app.routers.orgs import router as orgs_router
from app.routers.apikeys import router as apikeys_router
from app.routers.certs import router as certs_router
from agents.routers import router as agents_router
from app.aegis.router import router as aegis_router
from app.ota_manager import OtaStateMachine, ota_timeout_watcher
from app.metrics import (
    metrics_middleware, active_devices, total_devices, mqtt_messages_received,
    v2g_active_discharges, device_soc, telemetry_points_total, command_queue_depth,
    command_queue_delivered_total, command_queue_expired_total, device_lifecycle_transitions,
    shadow_updates_total, device_cert_rejected_total,
)
from app.models import (
    Device, DeviceStatus, DeviceLifecycle, Firmware, Telemetry, CommandQueue,
    CommandStatus, DeviceShadow, OtaDeployment, OtaStatus, DeviceCertificate,
)
from app.utils import utcnow
from app.audit import log_action
from app.event_emitter import emit_event

logging.basicConfig(level=getattr(logging, settings.log_level.upper()))
logger = logging.getLogger(__name__)


# ── P0 UC-23 rule 5: HMAC download tokens for the firmware C2 channel ──────

def _firmware_token_sig(device_id: str, sha256_hash: str, exp: int) -> str:
    msg = f"{device_id}:{sha256_hash}:{exp}".encode()
    return hmac.new(settings.jwt_secret_key.encode(), msg, hashlib.sha256).hexdigest()


def issue_firmware_download_token(device_id: str, sha256_hash: str) -> tuple[str, int]:
    """Short-lived signed token embedded in OTA commands (strict mode only)."""
    exp = int(utcnow().timestamp()) + settings.firmware_token_ttl_seconds
    return _firmware_token_sig(device_id, sha256_hash, exp), exp


def verify_firmware_download_token(device_id: str, sha256_hash: str, exp: int, token: str) -> bool:
    if int(exp) < int(utcnow().timestamp()):
        return False
    expected = _firmware_token_sig(device_id, sha256_hash, int(exp))
    return hmac.compare_digest(expected, token or "")


async def _record_telemetry(device: Device, payload: dict):
    """Persist a telemetry data point (Feature 1)."""
    try:
        point = Telemetry(
            device_id=device.id,
            timestamp=utcnow(),
            signal_strength=payload.get("signal_strength"),
            uptime_percentage=payload.get("uptime_percentage"),
            soc=payload.get("soc"),
            soh=payload.get("soh"),
            battery_temp=payload.get("battery_temp"),
            plug_status=payload.get("plug_status"),
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
            cpu_usage=payload.get("cpu_usage"),
            memory_usage=payload.get("memory_usage"),
            temperature=payload.get("temperature"),
        )
        async with async_session_factory() as db:
            db.add(point)
            await db.commit()
        telemetry_points_total.labels(device=device.name).inc()
    except Exception:
        logger.debug("Telemetry record failed", exc_info=True)


async def _check_geofences(device_id: str):
    """Check device position against geofences (Feature 2)."""
    try:
        from app.geofence_checker import check_device_position, build_geofence_alerts
        from app.alert_engine import AlertEngine
        async with async_session_factory() as db:
            result = await db.execute(select(Device).where(Device.id == device_id))
            device = result.scalar_one_or_none()
            if not device or device.latitude is None or device.longitude is None:
                return
            events = await check_device_position(db, device)
            if events:
                anomalies = await build_geofence_alerts(events, db)
                if anomalies:
                    engine = AlertEngine(db)
                    await engine.process_anomalies(anomalies)
                await emit_event(db, "geofence.event", {
                    "device_id": device_id,
                    "events": [{"geofence_id": e.geofence_id, "type": e.event_type} for e in events],
                })
    except Exception:
        logger.debug("Geofence check failed", exc_info=True)


async def _flush_command_queue(device_id: str):
    """Deliver queued commands to a reconnected device (Feature 5)."""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(CommandQueue)
                .where(CommandQueue.device_id == device_id, CommandQueue.status == CommandStatus.queued)
                .order_by(CommandQueue.created_at.asc())
            )
            queued = result.scalars().all()
            if not queued:
                return
            now = utcnow()
            delivered = 0
            for cmd in queued:
                if cmd.expires_at and cmd.expires_at < now:
                    cmd.status = CommandStatus.expired
                    command_queue_expired_total.labels(command_type=cmd.command_type).inc()
                    continue
                payload = json.loads(cmd.payload)
                topic = f"iot/fleet/{device_id}/command/{cmd.command_type}"
                success = mqtt_client.publish_raw(topic, json.dumps(payload))
                if success:
                    cmd.status = CommandStatus.delivered
                    cmd.delivered_at = now
                    delivered += 1
                    command_queue_delivered_total.labels(command_type=cmd.command_type).inc()
                else:
                    cmd.retry_count += 1
                    if cmd.retry_count >= cmd.max_retries:
                        cmd.status = CommandStatus.failed
            await db.commit()
            if delivered:
                logger.info("Flushed %d queued commands to device %s", delivered, device_id)
                await _update_queue_depth()
    except Exception:
        logger.debug("Command queue flush failed", exc_info=True)


async def _update_queue_depth():
    """Update the command queue depth gauge."""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(CommandQueue).where(CommandQueue.status == CommandStatus.queued)
            )
            command_queue_depth.set(len(result.scalars().all()))
    except Exception:
        pass


async def _sync_shadow_to_device(device_id: str):
    """Push the latest desired shadow state to a device on reconnect (Feature 7)."""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(DeviceShadow)
                .where(DeviceShadow.device_id == device_id, DeviceShadow.state == "desired")
                .order_by(DeviceShadow.version.desc())
                .limit(1)
            )
            shadow = result.scalar_one_or_none()
            if shadow:
                state = json.loads(shadow.payload)
                mqtt_client.publish_shadow_desired(device_id, state)
                logger.info("Synced desired shadow v%d to device %s", shadow.version, device_id)
    except Exception:
        logger.debug("Shadow sync failed", exc_info=True)


async def _revoked_device_ids() -> set[str]:
    """Device ids with NO active certificate remaining (strict-mode gate).

    Rotation issues a new `active` row before/alongside revoking the old one,
    so a rotated device keeps flowing while a fully-revoked one is blocked.
    Certificate-level revocation itself is enforced by the broker via CRL;
    this is defense-in-depth for the application layer.
    """
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(DeviceCertificate.device_id, DeviceCertificate.status).where(
                    DeviceCertificate.device_id.isnot(None)
                )
            )
            statuses: dict[str, list[str]] = {}
            for device_id, status in result.all():
                statuses.setdefault(device_id, []).append(status)
            return {
                d for d, sts in statuses.items() if "active" not in sts
            }
    except Exception:
        return set()


async def handle_mqtt_register(payload: dict, verified_id: str | None = None):
    """Device registration over MQTT.

    P0 UC-24/UC-25 identity rules:
      - verified_id comes from the per-device topic `iot/fleet/{id}/register`,
        which the broker ACL binds to the TLS cert CN. In strict mode,
        registrations without a verified id are REJECTED (spoofable legacy
        shared topic).
      - JITP: an unknown device_id presenting on its own verified topic is
        provisioned into the organization recorded on its issued certificate.
    """
    async with async_session_factory() as db:
        strict = settings.auth_mode == "strict"

        if strict and not verified_id:
            device_cert_rejected_total.labels(reason="unverified_register_topic").inc()
            logger.warning("Rejected register via legacy topic in strict mode: %s", payload.get("name"))
            return

        name = payload.get("name", "unknown")
        device_id = payload.get("device_id") or verified_id
        mqtt_id = payload.get("device_id")  # the MQTT client's identifier (MAC for ESP32)
        city = payload.get("city")

        if strict and verified_id:
            if device_id and device_id != verified_id:
                device_cert_rejected_total.labels(reason="identity_mismatch").inc()
                logger.warning(
                    "Register identity mismatch: topic CN=%s payload device_id=%s",
                    verified_id, device_id,
                )
                return
            device_id = verified_id
            mqtt_id = mqtt_id or verified_id

        # Revocation check (defense-in-depth behind the broker CRL)
        if strict and device_id and device_id in await _revoked_device_ids():
            device_cert_rejected_total.labels(reason="revoked_cert").inc()
            logger.warning("Rejected register for revoked device cert: %s", device_id)
            return

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
            if city:
                existing.city = city
            if was_offline:
                active_devices.inc()
                # Flush queued commands on reconnect (Feature 5)
                asyncio.create_task(_flush_command_queue(existing.id))
                # Sync desired shadow (Feature 7)
                asyncio.create_task(_sync_shadow_to_device(existing.id))
            await db.commit()
            await log_action(db, "system", "device.reconnect", "device", existing.id, {"name": name})
            await emit_event(db, "device.reconnected", {"device_id": existing.id, "name": name})
        else:
            # JITP (UC-25): resolve the org from the device's issued certificate.
            org_id = DEFAULT_ORG_ID
            if device_id:
                cert_result = await db.execute(
                    select(DeviceCertificate).where(
                        DeviceCertificate.device_id == device_id,
                        DeviceCertificate.status.in_(("active", "issued")),
                    ).order_by(DeviceCertificate.issued_at.desc()).limit(1)
                )
                cert_row = cert_result.scalar_one_or_none()
                if cert_row:
                    org_id = cert_row.org_id or DEFAULT_ORG_ID
                elif strict:
                    device_cert_rejected_total.labels(reason="unknown_identity").inc()
                    logger.warning(
                        "JITP rejected: no active certificate for CN/device '%s'", device_id
                    )
                    return

            device = Device(
                id=device_id,
                name=name,
                mqtt_client_id=mqtt_id,
                firmware_version=payload.get("firmware_version", "1.0.0"),
                status=DeviceStatus.online,
                last_seen=utcnow(),
                ip_address=payload.get("ip_address", ""),
                city=city,
                org_id=org_id,
            )
            db.add(device)
            await db.commit()
            await db.refresh(device)
            active_devices.inc()
            total_devices.inc()
            await log_action(db, "system", "device.register", "device", device.id,
                             {"name": name, "city": city, "org_id": org_id})
            await emit_event(db, "device.registered", {"device_id": device.id, "name": name, "city": city})
            logger.info("MQTT auto-registered device: %s (id=%s, mqtt_id=%s, org=%s)",
                        name, device_id, mqtt_id, org_id)
    mqtt_messages_received.labels(topic="register").inc()


async def handle_mqtt_heartbeat(device_id: str, payload: dict):
    # P0 UC-25 defense-in-depth: ignore heartbeats from revoked identities.
    if settings.auth_mode == "strict" and device_id in await _revoked_device_ids():
        device_cert_rejected_total.labels(reason="revoked_cert").inc()
        logger.warning("Dropped heartbeat from revoked device cert: %s", device_id)
        return

    async with async_session_factory() as db:
        result = await db.execute(select(Device).where(Device.id == device_id))
        device = result.scalar_one_or_none()
        if device:
            was_offline = device.status == DeviceStatus.offline
            device.last_seen = utcnow()
            device.uptime_percentage = payload.get("uptime_percentage", 100.0)
            device.signal_strength = payload.get("signal_strength", 0)
            device.status = DeviceStatus.online
            if "city" in payload and payload["city"]:
                device.city = payload["city"]
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
            if "latitude" in payload and "longitude" in payload:
                device.latitude = float(payload["latitude"])
                device.longitude = float(payload["longitude"])
            await db.commit()
            if was_offline:
                active_devices.inc()
                asyncio.create_task(_flush_command_queue(device_id))
                asyncio.create_task(_sync_shadow_to_device(device_id))
            # Feature 1: record telemetry
            asyncio.create_task(_record_telemetry(device, payload))
            # Feature 2: geofence check
            if "latitude" in payload and "longitude" in payload:
                asyncio.create_task(_check_geofences(device_id))
    mqtt_messages_received.labels(topic="heartbeat").inc()


async def handle_mqtt_v2g_status(device_id: str, payload: dict):
    """Handle V2G status reports from devices (Bug 1 fix + Feature 7)."""
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(Device).where(Device.id == device_id))
            device = result.scalar_one_or_none()
            if not device:
                return
            action = payload.get("action", "idle")
            if "soc" in payload:
                device.soc = float(payload["soc"])
            if "soh" in payload:
                device.soh = float(payload["soh"])
            if "plug_status" in payload:
                device.plug_status = payload["plug_status"]
            # Record reported shadow state
            shadow = DeviceShadow(
                device_id=device_id,
                state="reported",
                payload=json.dumps(payload),
                version=1,
                timestamp=utcnow(),
            )
            db.add(shadow)
            await db.commit()
            shadow_updates_total.labels(state="reported").inc()
            logger.debug("V2G status from %s: action=%s", device_id, action)
    except Exception:
        logger.debug("V2G status handler failed", exc_info=True)
    mqtt_messages_received.labels(topic="status_v2g").inc()


async def _recover_ota_timeout_watches():
    """Recover OTA timeout watchers for non-terminal deployments on restart (Bug 6)."""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(OtaDeployment).where(
                    OtaDeployment.status.in_([
                        OtaStatus.pending, OtaStatus.downloading,
                        OtaStatus.applying, OtaStatus.verifying,
                    ])
                )
            )
            stuck = result.scalars().all()
            for dep in stuck:
                from app.models import Device
                dev_result = await db.execute(select(Device).where(Device.id == dep.device_id))
                dev = dev_result.scalar_one_or_none()
                mqtt_id = (dev.mqtt_client_id or dev.id) if dev else dep.device_id
                ota_timeout_watcher.start_watch(dep.id, mqtt_id)
                logger.info("Recovered OTA timeout watch for deployment %s", dep.id[:8])
    except Exception:
        logger.warning("OTA timeout recovery failed", exc_info=True)


async def _ota_scheduler_loop():
    """Background loop for scheduled OTA campaigns (Feature 4)."""
    from app.routers.scheduled_ota import run_due_schedules
    logger.info("OTA scheduler started (interval=%ss)", settings.ota_scheduler_interval_seconds)
    while True:
        try:
            async with async_session_factory() as db:
                await run_due_schedules(db)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("OTA scheduler cycle failed")
        await asyncio.sleep(settings.ota_scheduler_interval_seconds)


async def _command_queue_flusher_loop():
    """Background loop that flushes queued commands to online devices (Feature 5)."""
    logger.info("Command queue flusher started (interval=%ss)", settings.command_queue_flush_interval_seconds)
    while True:
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(Device).where(Device.status == DeviceStatus.online)
                )
                online = result.scalars().all()
                for dev in online:
                    await _flush_command_queue_for_device(db, dev.id)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Command queue flusher cycle failed")
        await asyncio.sleep(settings.command_queue_flush_interval_seconds)


async def _flush_command_queue_for_device(db, device_id: str):
    """Flush queued commands for a specific device within a shared session."""
    result = await db.execute(
        select(CommandQueue)
        .where(CommandQueue.device_id == device_id, CommandQueue.status == CommandStatus.queued)
        .order_by(CommandQueue.created_at.asc())
    )
    queued = result.scalars().all()
    now = utcnow()
    for cmd in queued:
        if cmd.expires_at and cmd.expires_at < now:
            cmd.status = CommandStatus.expired
            command_queue_expired_total.labels(command_type=cmd.command_type).inc()
            continue
        payload = json.loads(cmd.payload)
        topic = f"iot/fleet/{device_id}/command/{cmd.command_type}"
        success = mqtt_client.publish_raw(topic, json.dumps(payload))
        if success:
            cmd.status = CommandStatus.delivered
            cmd.delivered_at = now
            command_queue_delivered_total.labels(command_type=cmd.command_type).inc()
        else:
            cmd.retry_count += 1
            if cmd.retry_count >= cmd.max_retries:
                cmd.status = CommandStatus.failed


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Fleet Commander backend (role=%s, auth_mode=%s)...",
                settings.role, settings.auth_mode)
    validate_settings()
    os.makedirs(settings.firmware_storage_path, exist_ok=True)

    # P0 UC-27: retry DB init — production profile may race postgres health.
    last_err = None
    for attempt in range(10):
        try:
            await init_db()
            last_err = None
            break
        except Exception as e:
            last_err = e
            logger.warning("DB init attempt %d failed: %s (retrying in 3s)", attempt + 1, e)
            await asyncio.sleep(3)
    if last_err is not None:
        raise RuntimeError(f"Database unavailable after retries: {last_err}")

    loop = asyncio.get_running_loop()

    if settings.role == "api":
        # P0 HA split: API replicas serve HTTP only. MQTT subscription,
        # Aegis scheduler, OTA watchers and queue flusher are LEADER-only so
        # replicas never double-consume commands or duplicate schedulers.
        logger.info("ROLE=api replica: skipping MQTT + background schedulers")
        yield
        return

    mqtt_client.set_event_loop(loop)
    mqtt_client.on_ota_status(OtaStateMachine.handle_ota_status)
    mqtt_client.on_heartbeat(handle_mqtt_heartbeat)
    mqtt_client.on_register(handle_mqtt_register)
    mqtt_client.on_v2g_status(handle_mqtt_v2g_status)  # Bug 1 fix: wire V2G status handler
    mqtt_client.connect()

    from app.aegis.engine import AegisEngine, set_engine
    from app.aegis.scheduler import AegisScheduler
    aegis_engine = AegisEngine()
    set_engine(aegis_engine)

    # Bug 5 fix: load Aegis rule config overrides from DB at startup
    try:
        from app.aegis.rules import load_rule_configs, merge_configs
        async with async_session_factory() as db:
            configs = await load_rule_configs(db)
            if configs:
                await merge_configs(aegis_engine.registry, configs)
                logger.info("Applied %d Aegis rule config overrides", len(configs))
    except Exception:
        logger.warning("Failed to load Aegis rule configs", exc_info=True)

    scheduler = AegisScheduler(engine=aegis_engine, interval=settings.aegis_scrape_interval)
    aegis_task = asyncio.create_task(scheduler.run())
    logger.info("Aegis auto-remediation engine started (interval=%ss)", settings.aegis_scrape_interval)

    # Bug 6 fix: recover OTA timeout watches for stuck deployments
    await _recover_ota_timeout_watches()

    # Feature 4: start OTA scheduler loop
    ota_scheduler_task = asyncio.create_task(_ota_scheduler_loop())

    # Feature 5: start command queue flusher loop
    cmd_queue_task = asyncio.create_task(_command_queue_flusher_loop())

    # Auto-create GPS demo firmware record if it doesn't exist
    try:
        async with async_session_factory() as db:
            existing = await db.execute(select(Firmware).where(Firmware.version == "2.0.0-gps"))
            if not existing.scalar_one_or_none():
                os.makedirs(settings.firmware_storage_path, exist_ok=True)
                fw_path = os.path.join(settings.firmware_storage_path, "firmware_gps.bin")
                content = b"GPS_ENABLED_FIRMWARE_DEMO_" + b"\x00" * 1000
                with open(fw_path, "wb") as f:
                    f.write(content)
                fw = Firmware(
                    version="2.0.0-gps",
                    filename="firmware_gps.bin",
                    sha256_hash=hashlib.sha256(content).hexdigest(),
                    binary_path=fw_path,
                    file_size=len(content),
                )
                db.add(fw)
                await db.commit()
                logger.info("Auto-created GPS demo firmware: 2.0.0-gps")
    except Exception as e:
        logger.warning("Could not auto-create GPS firmware: %s", e)

    logger.info("Fleet Commander backend started.")
    yield
    scheduler.stop()
    aegis_task.cancel()
    ota_scheduler_task.cancel()
    cmd_queue_task.cancel()
    for task in [aegis_task, ota_scheduler_task, cmd_queue_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass
    mqtt_client.disconnect()
    logger.info("Fleet Commander backend shut down.")


# P0 rule 4: docs may be disabled entirely in production (DOCS_ENABLED=false).
_docs_kwargs = {}
if not settings.docs_enabled:
    _docs_kwargs = {"docs_url": None, "redoc_url": None, "openapi_url": None}

app = FastAPI(
    title="Fleet Commander",
    description="Production-grade IoT device management module",
    version="2.1.0",
    lifespan=lifespan,
    **_docs_kwargs,
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
app.include_router(telemetry_router)
app.include_router(geofences_router)
app.include_router(lifecycle_router)
app.include_router(scheduled_ota_router)
app.include_router(command_queue_router)
app.include_router(audit_router)
app.include_router(shadow_router)
app.include_router(predictive_router)
app.include_router(webhooks_router)
app.include_router(provisioning_router)
app.include_router(orgs_router)      # P0 UC-26
app.include_router(apikeys_router)   # P0 UC-23
app.include_router(certs_router)     # P0 UC-25


@app.get("/health")
async def health():
    """Liveness: process is up. Never auth-gated (P0 rule 4)."""
    return {"status": "ok", "role": settings.role, "auth_mode": settings.auth_mode}


@app.get("/health/ready")
async def health_ready():
    """Readiness: DB reachable (+ MQTT for the leader). Never auth-gated."""
    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    if settings.role == "api":
        # Replicas have no MQTT loop — DB-only readiness.
        if not db_ok:
            raise HTTPException(status_code=503, detail={"database": False})
        return {"status": "ready", "role": settings.role, "database": True}

    mqtt_ok = bool(mqtt_client._connected)
    if not db_ok or not mqtt_ok:
        raise HTTPException(
            status_code=503,
            detail={"database": db_ok, "mqtt": mqtt_ok},
        )
    return {"status": "ready", "role": settings.role, "database": True, "mqtt": True}


@app.get("/firmware/{filename}")
async def serve_firmware(filename: str, request: Request):
    storage = os.path.realpath(settings.firmware_storage_path)
    file_path = os.path.realpath(os.path.join(storage, filename))
    if not file_path.startswith(storage + os.sep):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Firmware file not found")

    # P0 UC-23 rule 5: firmware download is a C2 channel — in strict mode a
    # short-lived HMAC token (issued when the OTA command was published) is
    # mandatory: ?did=<device_id>&exp=<unix>&token=<hmac>.
    if settings.auth_mode == "strict":
        did = request.query_params.get("did", "")
        exp = request.query_params.get("exp", "0")
        token = request.query_params.get("token", "")
        result = await db_lookup_firmware_hash(filename)
        if result is None:
            raise HTTPException(status_code=404, detail="Firmware file not found")
        sha256_hash = result
        if not did or not verify_firmware_download_token(did, sha256_hash, int(exp or 0), token):
            device_cert_rejected_total.labels(reason="firmware_token_invalid").inc()
            raise HTTPException(status_code=401, detail="Invalid or expired firmware token")

    return FileResponse(file_path, media_type="application/octet-stream", filename=filename)


async def db_lookup_firmware_hash(filename: str):
    async with async_session_factory() as db:
        result = await db.execute(select(Firmware).where(Firmware.filename == filename))
        fw = result.scalar_one_or_none()
        return fw.sha256_hash if fw else None


@app.get("/architect-diagram", response_class=HTMLResponse)
async def architect_diagram():
    diagram_path = os.path.join(os.path.dirname(__file__), "static", "architecture-diagram.html")
    with open(diagram_path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/health/mqtt")
async def health_mqtt():
    return {"connected": mqtt_client._connected}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
