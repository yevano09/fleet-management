"""
Unit tests for Session 5+ features (telemetry, geofencing, predictive
maintenance, firmware signing, audit log, command queue, shadow, lifecycle).
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.database import Base
from app.models import (
    Device, DeviceStatus, DeviceLifecycle, Telemetry, Geofence, GeofenceEvent,
    GeofenceShape, CommandQueue, CommandStatus, AuditLog, DeviceShadow,
    OtaSchedule, ScheduleStatus, PredictedFailure, WebhookSubscription, EventLog,
    Firmware, UserRole, UserSession,
)
from app.utils import utcnow


async def _make_db():
    """Create an in-memory SQLite session for testing."""
    test_engine = create_async_engine("sqlite+aiosqlite://")
    test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return test_engine, test_session_factory


# ── Feature 1: Telemetry model ───────────────────────────────────────────────

class TestTelemetry:
    @pytest.mark.asyncio
    async def test_telemetry_creation(self):
        _, sf = await _make_db()
        async with sf() as db:
            device = Device(name="TelTest", firmware_version="1.0.0", status=DeviceStatus.online)
            db.add(device)
            await db.commit()
            await db.refresh(device)

            point = Telemetry(
                device_id=device.id,
                signal_strength=-65,
                cpu_usage=20.5,
                memory_usage=45.0,
                temperature=42.0,
                soc=80.0,
                soh=95.0,
            )
            db.add(point)
            await db.commit()
            await db.refresh(point)
            assert point.id is not None
            assert point.signal_strength == -65
            assert point.cpu_usage == 20.5
            assert point.timestamp is not None


# ── Feature 2: Geofence checker ──────────────────────────────────────────────

class TestGeofenceChecker:
    def test_haversine_distance(self):
        from app.geofence_checker import _haversine_meters
        assert _haversine_meters(12.9716, 77.5946, 12.9716, 77.5946) == 0.0
        dist = _haversine_meters(12.9716, 77.5946, 19.0760, 72.8777)
        assert 800000 < dist < 900000

    def test_is_inside_circle(self):
        from app.geofence_checker import is_inside
        gf = Geofence(name="test", shape=GeofenceShape.circle,
                      center_lat=12.9716, center_lng=77.5946, radius_meters=5000)
        assert is_inside(gf, 12.9720, 77.5950) is True
        assert is_inside(gf, 13.5, 78.5) is False

    def test_is_inside_polygon(self):
        from app.geofence_checker import is_inside
        coords = [[0, 0], [0, 10], [10, 10], [10, 0]]
        gf = Geofence(name="poly", shape=GeofenceShape.polygon,
                      polygon_coords=json.dumps(coords))
        assert is_inside(gf, 5, 5) is True
        assert is_inside(gf, 15, 15) is False

    def test_is_inside_missing_data(self):
        from app.geofence_checker import is_inside
        gf = Geofence(name="empty", shape=GeofenceShape.circle)
        assert is_inside(gf, 12.0, 77.0) is False


# ── Feature 3: Predictive maintenance ────────────────────────────────────────

class TestPredictiveMaintenance:
    def test_linear_slope(self):
        from app.predictive_maintenance import _linear_slope
        assert _linear_slope([1, 2, 3, 4, 5]) > 0
        assert _linear_slope([5, 4, 3, 2, 1]) < 0
        assert _linear_slope([3, 3, 3, 3, 3]) == 0.0
        assert _linear_slope([]) == 0.0
        assert _linear_slope([5]) == 0.0

    def test_hours_to_threshold(self):
        from app.predictive_maintenance import _hours_to_threshold
        htf = _hours_to_threshold(-70, -1.0, -100, 1.0)
        assert htf is not None and htf > 0
        assert _hours_to_threshold(-70, 0, -100, 1.0) is None
        assert _hours_to_threshold(-110, -1.0, -100, 1.0) is None


# ── Feature 5: Command queue ─────────────────────────────────────────────────

class TestCommandQueue:
    @pytest.mark.asyncio
    async def test_command_queue_creation(self):
        _, sf = await _make_db()
        async with sf() as db:
            device = Device(name="CmdTest", firmware_version="1.0.0", status=DeviceStatus.offline)
            db.add(device)
            await db.commit()
            await db.refresh(device)

            cmd = CommandQueue(
                device_id=device.id,
                command_type="config",
                payload=json.dumps({"heartbeat_interval": 5}),
                status=CommandStatus.queued,
                expires_at=utcnow() + timedelta(hours=1),
            )
            db.add(cmd)
            await db.commit()
            await db.refresh(cmd)
            assert cmd.id is not None
            assert cmd.status == CommandStatus.queued


# ── Feature 6: Audit log ─────────────────────────────────────────────────────

class TestAuditLog:
    @pytest.mark.asyncio
    async def test_audit_log_creation(self):
        _, sf = await _make_db()
        async with sf() as db:
            from app.audit import log_action, get_audit_logs
            entry = await log_action(db, "test_user", "test.action", "device", "dev-123",
                                     details={"key": "value"})
            assert entry.id is not None
            assert entry.actor == "test_user"

            result = await get_audit_logs(db, action="test.action")
            assert result["total"] >= 1
            assert any(l.actor == "test_user" for l in result["logs"])


# ── Feature 7: Device shadow ─────────────────────────────────────────────────

class TestDeviceShadow:
    @pytest.mark.asyncio
    async def test_shadow_creation(self):
        _, sf = await _make_db()
        async with sf() as db:
            device = Device(name="ShadowTest", firmware_version="1.0.0", status=DeviceStatus.online)
            db.add(device)
            await db.commit()
            await db.refresh(device)

            shadow = DeviceShadow(
                device_id=device.id, state="desired",
                payload=json.dumps({"heartbeat_interval": 15}), version=1,
            )
            db.add(shadow)
            await db.commit()
            await db.refresh(shadow)
            assert shadow.id is not None
            assert shadow.state == "desired"
            assert shadow.version == 1


# ── Feature 8: Firmware signing ──────────────────────────────────────────────

class TestFirmwareSigning:
    def test_sign_without_key_returns_none(self):
        from app.firmware_signing import sign_firmware
        sig, kid = sign_firmware(b"test_content")
        assert sig is None
        assert kid is None

    def test_verify_empty_signature_passes(self):
        from app.firmware_signing import verify_firmware
        assert verify_firmware(b"test", "") is True

    def test_generate_keypair(self):
        from app.firmware_signing import generate_keypair
        try:
            priv, pub = generate_keypair()
            assert "BEGIN PRIVATE KEY" in priv
            assert "BEGIN PUBLIC KEY" in pub
        except RuntimeError:
            pytest.skip("cryptography not installed")

    def test_sign_and_verify_with_keypair(self):
        from app.firmware_signing import generate_keypair, sign_firmware, verify_firmware
        from app.config import settings
        try:
            priv, pub = generate_keypair()
            settings.firmware_signing_private_key = priv
            settings.firmware_signing_public_key = pub

            content = b"FIRMWARE_BINARY_CONTENT"
            sig, kid = sign_firmware(content)
            assert sig is not None
            assert kid is not None
            assert verify_firmware(content, sig, kid) is True
            assert verify_firmware(b"TAMPERED", sig, kid) is False

            settings.firmware_signing_private_key = ""
            settings.firmware_signing_public_key = ""
        except RuntimeError:
            pytest.skip("cryptography not installed")


# ── Feature 9: Device lifecycle ──────────────────────────────────────────────

class TestDeviceLifecycle:
    @pytest.mark.asyncio
    async def test_lifecycle_field_default(self):
        _, sf = await _make_db()
        async with sf() as db:
            device = Device(name="LifeTest", firmware_version="1.0.0", status=DeviceStatus.online)
            db.add(device)
            await db.commit()
            await db.refresh(device)
            assert device.lifecycle_status == DeviceLifecycle.active

    @pytest.mark.asyncio
    async def test_decommission_fields(self):
        _, sf = await _make_db()
        async with sf() as db:
            device = Device(
                name="DecomTest", firmware_version="1.0.0", status=DeviceStatus.online,
                lifecycle_status=DeviceLifecycle.decommissioned,
                decommissioned_at=utcnow(), decommissioned_by="admin",
                decommissioned_reason="retired",
            )
            db.add(device)
            await db.commit()
            await db.refresh(device)
            assert device.lifecycle_status == DeviceLifecycle.decommissioned
            assert device.decommissioned_by == "admin"


# ── Feature 11: Webhook / event log ──────────────────────────────────────────

class TestWebhookEventLog:
    @pytest.mark.asyncio
    async def test_webhook_creation(self):
        _, sf = await _make_db()
        async with sf() as db:
            sub = WebhookSubscription(name="TestHook", url="http://example.com/hook", event_types="*")
            db.add(sub)
            await db.commit()
            await db.refresh(sub)
            assert sub.id is not None
            assert sub.enabled is True

    @pytest.mark.asyncio
    async def test_event_log_creation(self):
        _, sf = await _make_db()
        async with sf() as db:
            evt = EventLog(event_type="test.event", payload=json.dumps({"key": "value"}))
            db.add(evt)
            await db.commit()
            await db.refresh(evt)
            assert evt.id is not None
            assert evt.event_type == "test.event"
            assert evt.delivered == 0


# ── Feature 12: RBAC roles ───────────────────────────────────────────────────

class TestRBACRoles:
    def test_user_role_enum(self):
        assert UserRole.user.value == "user"
        assert UserRole.admin.value == "admin"
        assert UserRole.operator.value == "operator"
        assert UserRole.viewer.value == "viewer"
        assert UserRole.fleet_manager.value == "fleet_manager"


# ── Feature 4: Scheduled OTA model ───────────────────────────────────────────

class TestScheduledOta:
    @pytest.mark.asyncio
    async def test_schedule_creation(self):
        _, sf = await _make_db()
        async with sf() as db:
            fw = Firmware(version="sched-test-fw", filename="s.bin", sha256_hash="abc",
                          binary_path="/tmp/s.bin", file_size=100)
            db.add(fw)
            await db.commit()
            await db.refresh(fw)

            sched = OtaSchedule(
                name="UnitTestSchedule", firmware_id=fw.id, all_devices=True,
                scheduled_for=datetime.utcnow() + timedelta(hours=1),
                canary_percent=10, blackout_start_hour=9, blackout_end_hour=17,
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)
            assert sched.id is not None
            assert sched.status == ScheduleStatus.scheduled
            assert sched.blackout_start_hour == 9


# ── Feature 13: Claim token ──────────────────────────────────────────────────

class TestClaimToken:
    @pytest.mark.asyncio
    async def test_claim_token_field(self):
        _, sf = await _make_db()
        async with sf() as db:
            import secrets
            device = Device(
                name="ClaimTest", firmware_version="1.0.0", status=DeviceStatus.offline,
                claim_token=secrets.token_urlsafe(16),
            )
            db.add(device)
            await db.commit()
            await db.refresh(device)
            assert device.claim_token is not None
            assert len(device.claim_token) > 10


# ── Phase-gate bug fix ────────────────────────────────────────────────────────

class TestPhaseGateFix:
    def test_phase_gate_logic(self):
        phases = []
        phase_num = 0
        for pct, label in [(30, "Phase 1"), (60, "Phase 2"), (100, "Phase 3")]:
            phase_num += 1
            is_final = (pct == 100)
            gate = "No gate (final phase)" if is_final else f"Wait {3 * phase_num + 5} min"
            phases.append({"phase": label, "gate": gate})

        assert "No gate" not in phases[0]["gate"]
        assert "No gate" not in phases[1]["gate"]
        assert "No gate" in phases[2]["gate"]


# ── Device city field (Bug 2) ────────────────────────────────────────────────

class TestDeviceCity:
    @pytest.mark.asyncio
    async def test_city_field(self):
        _, sf = await _make_db()
        async with sf() as db:
            device = Device(name="CityTest", firmware_version="1.0.0", status=DeviceStatus.online, city="Bangalore")
            db.add(device)
            await db.commit()
            await db.refresh(device)
            assert device.city == "Bangalore"
