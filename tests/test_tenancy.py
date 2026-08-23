"""P0 UC-26 + UC-25 — tenancy & JITP behaviour against an in-memory SQLite DB.

Runs the real `handle_mqtt_register` / `handle_mqtt_heartbeat` handlers with
`async_session_factory` redirected to a throwaway engine, so no server or
on-disk state is involved.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

import app.main as main
from app.config import settings, DEFAULT_ORG_ID
from app.database import Base
from app.models import Device, DeviceCertificate, Organization


@pytest.fixture()
def test_db(monkeypatch, tmp_path):
    """Redirect the app's session factory to a fresh file-backed SQLite DB.

    File-backed (not :memory:) so every pooled connection shares the schema —
    handlers and checks run on separate connections/event loops.
    """
    db_file = tmp_path / "tenancy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(Organization(id="org-alpha", name="Alpha", slug="alpha"))
            db.add(Organization(id=DEFAULT_ORG_ID, name="Default", slug="default"))
            await db.commit()

    asyncio.run(_setup())
    monkeypatch.setattr(main, "async_session_factory", factory)
    yield factory

    async def _teardown():
        await engine.dispose()

    asyncio.run(_teardown())


def _run(coro):
    return asyncio.run(coro)


async def _count_devices(factory):
    async with factory() as db:
        result = await db.execute(select(Device))
        return len(result.scalars().all())


async def _get_device_org(factory, device_id):
    async with factory() as db:
        result = await db.execute(select(Device).where(Device.id == device_id))
        d = result.scalar_one_or_none()
        return None if d is None else d.org_id


class TestJitRegistration:
    def test_verified_register_provisions_into_cert_org(self, test_db, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "strict")

        async def seed():
            async with test_db() as db:
                db.add(DeviceCertificate(
                    id=str(uuid.uuid4()),
                    device_id="jitp-dev",
                    org_id="org-alpha",
                    fingerprint_sha256="f" * 64,
                    pem="-----CERT-----",
                    serial="9001",
                    status="active",
                ))
                await db.commit()

        _run(seed())

        _run(main.handle_mqtt_register(
            {"device_id": "jitp-dev", "name": "JITP Device",
             "firmware_version": "1.0.0", "ip_address": "10.1.1.9"},
            verified_id="jitp-dev",
        ))

        assert _run(_count_devices(test_db)) == 1
        assert _run(_get_device_org(test_db, "jitp-dev")) == "org-alpha"

    def test_legacy_shared_topic_rejected_in_strict(self, test_db, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "strict")
        before = _run(_count_devices(test_db))

        _run(main.handle_mqtt_register(
            {"device_id": "ghost", "name": "Ghost"}, verified_id=None))

        assert _run(_count_devices(test_db)) == before  # nothing registered

    def test_legacy_topic_allowed_in_open_mode(self, test_db, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "open")

        _run(main.handle_mqtt_register(
            {"device_id": "legacy-1", "name": "Legacy"}, verified_id=None))

        assert _run(_get_device_org(test_db, "legacy-1")) == DEFAULT_ORG_ID

    def test_identity_mismatch_rejected(self, test_db, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "strict")

        _run(main.handle_mqtt_register(
            {"device_id": "other-id", "name": "Spoofer"}, verified_id="sim-001"))

        assert _run(_count_devices(test_db)) == 0

    def test_unknown_identity_rejected_in_strict(self, test_db, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "strict")

        # Verified topic but no certificate on file → JITP must refuse.
        _run(main.handle_mqtt_register(
            {"device_id": "no-cert", "name": "NoCert"}, verified_id="no-cert"))

        assert _run(_count_devices(test_db)) == 0

    def test_revoked_cert_register_rejected(self, test_db, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "strict")

        async def seed():
            async with test_db() as db:
                db.add(DeviceCertificate(
                    id=str(uuid.uuid4()),
                    device_id="rev-dev",
                    org_id="org-alpha",
                    fingerprint_sha256="a" * 64,
                    pem="-----CERT-----",
                    serial="9002",
                    status="revoked",
                ))
                await db.commit()

        _run(seed())
        _run(main.handle_mqtt_register(
            {"device_id": "rev-dev", "name": "Revoked"}, verified_id="rev-dev"))

        assert _run(_count_devices(test_db)) == 0


class TestHeartbeatIdentityGate:
    def test_revoked_device_heartbeat_dropped(self, test_db, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "strict")

        async def seed():
            async with test_db() as db:
                db.add(Device(
                    id="rev-live", name="RevLive",
                    firmware_version="1.0.0",
                ))
                db.add(DeviceCertificate(
                    id=str(uuid.uuid4()),
                    device_id="rev-live",
                    org_id=DEFAULT_ORG_ID,
                    fingerprint_sha256="b" * 64,
                    pem="-----CERT-----",
                    serial="9003",
                    status="revoked",
                ))
                await db.commit()

        _run(seed())
        _run(main.handle_mqtt_heartbeat("rev-live", {
            "uptime_percentage": 99.0, "signal_strength": -50,
        }))

        async def still_offline():
            async with test_db() as db:
                d = (await db.execute(
                    select(Device).where(Device.id == "rev-live"))).scalar_one()
                return d.status.value == "offline"

        # Heartbeat must NOT have flipped status to online.
        assert _run(still_offline()) is True

    def test_healthy_heartbeat_updates(self, test_db, monkeypatch):
        monkeypatch.setattr(settings, "auth_mode", "open")

        async def seed():
            async with test_db() as db:
                db.add(Device(id="ok-dev", name="Ok", firmware_version="1.0.0"))
                await db.commit()

        _run(seed())
        _run(main.handle_mqtt_heartbeat("ok-dev", {
            "uptime_percentage": 98.0, "signal_strength": -55,
        }))

        async def check():
            async with test_db() as db:
                d = (await db.execute(
                    select(Device).where(Device.id == "ok-dev"))).scalar_one()
                return d.status.value == "online" and d.signal_strength == -55

        assert _run(check()) is True


class TestScopeDevicesFilter:
    def test_query_gains_org_filter(self):
        from app.deps import scope_devices
        from sqlalchemy import select

        q = scope_devices(select(Device), {"role": "user", "org_id": "org-x"})
        sql = str(q.compile(compile_kwargs={"literal_binds": True}))
        assert "org_id IN" in sql
        assert "org-x" in sql

    def test_super_admin_query_unfiltered(self):
        from app.deps import scope_devices
        from sqlalchemy import select

        q = scope_devices(select(Device), {"role": "admin", "org_id": "*"})
        sql = str(q.compile()).upper()
        # Column list may mention org_id; tenancy filter means no WHERE on it.
        assert "WHERE" not in sql or "ORG_ID IN" not in sql
