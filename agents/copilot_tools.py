"""Copilot CrewAI tools (SRS Idea 5, M2.1).

Per-request BaseTool instances bound to the caller's org_id. Every tool is
READ-ONLY by construction (no mutating tool exists). Outputs are minimized +
redacted via agents.privacy BEFORE entering LLM context — tools return
LLM-safe dicts, never raw rows.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pydantic import BaseModel, Field

try:
    from crewai.tools import BaseTool

    CREWAI_OK = True
except Exception:  # pragma: no cover — backend runs mock path without crewai
    CREWAI_OK = False

    class BaseTool:  # type: ignore
        name = ""
        description = ""

        def __init__(self, *a, **k):
            pass

from app.database import async_session_factory
from agents import privacy as P

logger = logging.getLogger(__name__)


def _run(coro_factory):
    """Execute an async DB callable on a fresh session (crew threads are sync).

    Safe from both sync (CrewAI thread) and async (FastAPI endpoint, via
    asyncio.to_thread in the caller) contexts: if a loop is already running
    in THIS thread, fail loudly instead of deadlocking — callers must hop
    threads first (see copilot._call_tool).
    """
    import asyncio as _asyncio

    async def _inner():
        async with async_session_factory() as db:
            return await coro_factory(db)

    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(_inner())
    raise RuntimeError("copilot tool invoked inside a running event loop; hop threads first")


class _OrgTool(BaseTool):
    """orgs=None means unrestricted (super-admin); else concrete org list."""

    def __init__(self, orgs: Optional[list[str]] = None, **kwargs):
        super().__init__(**kwargs)
        self._orgs = orgs

    def _scope(self, query, column):
        if self._orgs is not None:
            query = query.where(column.in_(self._orgs))
        return query


class FleetStatusArgs(BaseModel):
    pass


class FleetStatusTool(_OrgTool):
    name: str = "fleet_status_summary"
    description: str = (
        "Fleet-wide counts: total/online/offline devices, active alerts by "
        "severity, open work orders, recent OTA failures. Use for 'how is the fleet' questions."
    )
    args_schema: type[BaseModel] = FleetStatusArgs

    def _run(self) -> dict:
        from sqlalchemy import select, func
        from app.models import Device, DeviceStatus, Alert, AlertStatus, WorkOrder

        async def _impl(db):
            total = (await db.execute(
                self._scope(select(func.count()).select_from(Device), Device.org_id))).scalar() or 0
            online = (await db.execute(
                self._scope(select(func.count()).select_from(Device), Device.org_id)
                .where(Device.status == DeviceStatus.online))).scalar() or 0
            alerts = (await db.execute(
                self._scope(select(Alert.severity, func.count()), Alert.org_id).where(
                    Alert.status.in_([AlertStatus.active, AlertStatus.acknowledged]),
                ).group_by(Alert.severity))).all()
            wo = (await db.execute(
                self._scope(select(func.count()).select_from(WorkOrder), WorkOrder.org_id).where(
                    WorkOrder.status == "open"))).scalar() or 0
            return {"total": total, "online": online, "offline": total - online,
                    "alerts_by_severity": {s: c for s, c in alerts}, "open_work_orders": wo}

        return _run(_impl)


class DeviceLookupArgs(BaseModel):
    name_hint: str = Field(description="Device name fragment, VIN suffix, or 'all' for a capped list")
    status: Optional[str] = Field(default=None, description="online|offline filter")


class DeviceLookupTool(_OrgTool):
    name: str = "device_lookup"
    description: str = (
        "Look up vehicles by name/VIN fragment. Returns redacted summaries "
        "(pseudonymous refs, degraded GPS, no IPs/MACs). Max 10 rows."
    )
    args_schema: type[BaseModel] = DeviceLookupArgs

    def _run(self, name_hint: str, status: Optional[str] = None) -> dict:
        from sqlalchemy import select, or_
        from app.models import Device, DeviceStatus as DS

        async def _impl(db):
            q = self._scope(select(Device), Device.org_id)
            if status in ("online", "offline"):
                q = q.where(Device.status == DS(status))
            if name_hint and name_hint.lower() != "all":
                like = f"%{name_hint}%"
                q = q.where(or_(Device.name.ilike(like), Device.vin.ilike(like),
                                Device.make.ilike(like), Device.model.ilike(like)))
            rows = (await db.execute(q.limit(10))).scalars().all()
            out = []
            for d in rows:
                m = P.minimize_device({
                    "name": d.name, "status": d.status.value if hasattr(d.status, "value") else d.status,
                    "firmware_version": d.firmware_version, "city": d.city,
                    "latitude": d.latitude, "longitude": d.longitude,
                    "signal_strength": d.signal_strength, "uptime_percentage": d.uptime_percentage,
                    "soc": d.soc, "soh": d.soh,
                })
                m["vin_suffix"] = P.mask_vin(d.vin)
                m["make"], m["model"] = d.make, d.model
                out.append(m)
            return {"devices": out, "total": len(out)}

        return _run(_impl)


class DtcLookupArgs(BaseModel):
    device_ref: str = Field(description="Device name fragment or VIN suffix")


class DtcLookupTool(_OrgTool):
    name: str = "dtc_lookup"
    description: str = (
        "Active DTC fault codes: for a named vehicle, or fleet-wide scan when "
        "device_ref is 'all'/empty. Human meanings included. Use for "
        "'engine light / check engine' questions."
    )
    args_schema: type[BaseModel] = DtcLookupArgs

    def _run(self, device_ref: str) -> dict:
        from sqlalchemy import select, or_
        from app.models import Device, Telemetry
        from app.obd.dtc import describe_dtc

        async def _impl(db):
            if not device_ref or device_ref.lower() == "all":
                return await self._fleet_scan(db, describe_dtc)
            like = f"%{device_ref}%"
            dev = (await db.execute(
                self._scope(select(Device), Device.org_id).where(or_(
                    Device.name.ilike(like), Device.vin.ilike(like)))
                .limit(1))).scalar_one_or_none()
            if not dev:
                return {"found": False}
            return await self._vehicle_codes(db, dev, describe_dtc)

        return _run(_impl)

    async def _vehicle_codes(self, db, dev, describe_dtc):
        from sqlalchemy import select
        from app.models import Telemetry

        tel = (await db.execute(
            select(Telemetry).where(Telemetry.device_id == dev.id)
            .order_by(Telemetry.timestamp.desc()).limit(20))).scalars().all()
        codes: list[str] = []
        for t in tel:
            if t.dtc_codes:
                try:
                    import json as _json

                    v = _json.loads(t.dtc_codes)
                    codes.extend(v if isinstance(v, list) else [])
                except Exception:
                    pass
        codes = sorted(set(codes))
        snap = None
        if tel:
            t = tel[0]
            snap = {"coolant_c": t.temperature, "signal_dbm": t.signal_strength,
                    "soc": t.soc, "soh": t.soh, "uptime_pct": t.uptime_percentage,
                    "fuel_pct": t.fuel_level_pct}
        return {"found": bool(codes), "vehicle": P.redact_name(dev.name),
                "dtc_codes": [{"code": c, "meaning": describe_dtc(c)} for c in codes],
                "telemetry_snapshot": snap}

    async def _fleet_scan(self, db, describe_dtc):
        from sqlalchemy import select
        from app.models import Device, Telemetry

        devs = (await db.execute(
            self._scope(select(Device), Device.org_id))).scalars().all()
        hits = []
        for dev in devs[:50]:
            tel = (await db.execute(
                select(Telemetry).where(Telemetry.device_id == dev.id)
                .order_by(Telemetry.timestamp.desc()).limit(5))).scalars().all()
            codes: list[str] = []
            for t in tel:
                if t.dtc_codes:
                    try:
                        import json as _json

                        v = _json.loads(t.dtc_codes)
                        codes.extend(v if isinstance(v, list) else [])
                    except Exception:
                        pass
            codes = sorted(set(codes))
            if codes:
                hits.append({"vehicle": P.redact_name(dev.name),
                             "dtc_codes": [{"code": c, "meaning": describe_dtc(c)} for c in codes]})
                if len(hits) >= 5:
                    break
        return {"found": bool(hits), "vehicles": hits}


class TelemetryArgs(BaseModel):
    device_ref: str = Field(description="Device name fragment or VIN suffix")
    hours: int = Field(default=6, description="Lookback hours, max 24")
    metrics: Optional[str] = Field(default=None, description="Comma list to keep; default core set")


class TelemetryTool(_OrgTool):
    name: str = "telemetry_snapshot"
    description: str = (
        "Recent minimized telemetry window for a vehicle (capped, projected "
        "columns only). Never returns raw tables."
    )
    args_schema: type[BaseModel] = TelemetryArgs

    def _run(self, device_ref: str, hours: int = 6, metrics: Optional[str] = None) -> dict:
        from sqlalchemy import select, or_
        from datetime import timedelta
        from app.models import Device, Telemetry
        from app.utils import utcnow

        async def _impl(db):
            like = f"%{device_ref}%"
            dev = (await db.execute(
                self._scope(select(Device), Device.org_id).where(or_(
                    Device.name.ilike(like), Device.vin.ilike(like)))
                .limit(1))).scalar_one_or_none()
            if not dev:
                return {"found": False}
            cutoff = utcnow() - timedelta(hours=min(max(hours, 1), 24))
            rows = (await db.execute(
                select(Telemetry).where(Telemetry.device_id == dev.id, Telemetry.timestamp >= cutoff)
                .order_by(Telemetry.timestamp.asc()).limit(48))).scalars().all()
            pts = [{"signal_strength": r.signal_strength, "temperature": r.temperature,
                    "soc": r.soc, "soh": r.soh, "uptime_percentage": r.uptime_percentage,
                    "fuel_level_pct": r.fuel_level_pct, "odometer_km": r.odometer_km,
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None} for r in rows]
            if metrics:
                keep = {m.strip() for m in metrics.split(",")} | {"timestamp"}
                pts = [{k: v for k, v in p.items() if k in keep} for p in pts]
            return {"found": True, "vehicle": P.redact_name(dev.name),
                    "points": P.minimize_telemetry(pts), "total": len(pts)}

        return _run(_impl)


class AlertSearchArgs(BaseModel):
    severity: Optional[str] = Field(default=None, description="critical|warning|info")
    limit: int = Field(default=10, description="Max alerts, capped at 20")


class AlertSearchTool(_OrgTool):
    name: str = "alert_search"
    description: str = "Recent active alerts (severity/type/message/count, no raw device ids)."
    args_schema: type[BaseModel] = AlertSearchArgs

    def _run(self, severity: Optional[str] = None, limit: int = 10) -> dict:
        from sqlalchemy import select
        from app.models import Alert, AlertStatus

        async def _impl(db):
            q = self._scope(select(Alert), Alert.org_id).where(
                Alert.status.in_([AlertStatus.active, AlertStatus.acknowledged]))
            if severity in ("critical", "warning", "info"):
                q = q.where(Alert.severity == severity)
            rows = (await db.execute(q.order_by(Alert.created_at.desc()).limit(min(limit, 20)))).scalars().all()
            return {"alerts": [
                {"type": a.type, "severity": a.severity, "message": P.redact_text(a.message or ""),
                 "count": a.count, "status": a.status.value, "has_work_order": bool(a.work_order_id)}
                for a in rows]}

        return _run(_impl)


class CargoStatusArgs(BaseModel):
    device_ref: Optional[str] = Field(default=None, description="Device name fragment; omit for fleet rollup")


class CargoStatusTool(_OrgTool):
    name: str = "cargo_status"
    description: str = (
        "Cold-chain status: latest bay temp/TTS per profiled vehicle, or fleet "
        "rollup of HIGH/MEDIUM risks. Use for spoilage/breach questions."
    )
    args_schema: type[BaseModel] = CargoStatusArgs

    def _run(self, device_ref: Optional[str] = None) -> dict:
        from sqlalchemy import select, or_
        from app.models import CargoProfile, CargoReading, Device
        from app.cargo_thermal import estimate_tts

        async def _impl(db):
            q = self._scope(select(CargoProfile), CargoProfile.org_id)
            profs = (await db.execute(q)).scalars().all()
            if device_ref:
                like = f"%{device_ref}%"
                dev = (await db.execute(
                    self._scope(select(Device), Device.org_id).where(or_(
                        Device.name.ilike(like), Device.vin.ilike(like))).limit(1))).scalar_one_or_none()
                if dev:
                    profs = [p for p in profs if p.device_id == dev.id]
            out = []
            dev_ids = [p.device_id for p in profs]
            names = {}
            if dev_ids:
                devs = (await db.execute(
                    select(Device).where(Device.id.in_(dev_ids)))).scalars().all()
                names = {d.id: d.name for d in devs}
            for p in profs:
                rows = (await db.execute(
                    select(CargoReading).where(CargoReading.device_id == p.device_id)
                    .order_by(CargoReading.timestamp.desc()).limit(12))).scalars().all()
                if not rows:
                    continue
                asc = [(r.timestamp, r.bay_temp_c) for r in reversed(rows)]
                est = estimate_tts(asc, p.temp_max_c, p.thermal_mass or 1.0)
                latest = rows[0]
                out.append({"vehicle": P.redact_name(names.get(p.device_id)),
                            "commodity": p.commodity,
                            "bay_temp_c": latest.bay_temp_c,
                            "threshold_c": p.temp_max_c,
                            "tts_minutes": est["tts_minutes"],
                            "risk": est["risk_level"],
                            "door_open": latest.door_open})
            high = [o for o in out if o["risk"] == "HIGH"]
            return {"vehicles": out[:10], "high_risk_count": len(high)}

        return _run(_impl)


class NotesSearchArgs(BaseModel):
    query: str = Field(description="Natural-language search over runbooks/SOPs/DTC guides")


class NotesSearchTool(_OrgTool):
    name: str = "service_notes_search"
    description: str = "RAG-lite search over service notes, SOPs and DTC guides (FTS-ranked snippets)."
    args_schema: type[BaseModel] = NotesSearchArgs

    def _run(self, query: str) -> dict:
        from sqlalchemy import select, or_
        from app.models import ServiceNote

        async def _impl(db):
            terms = [t for t in query.lower().split() if len(t) > 2][:6]
            if not terms:
                return {"notes": []}
            q = select(ServiceNote).where(or_(
                ServiceNote.org_id == "org-default"))
            for t in terms[:3]:
                like = f"%{t}%"
                q = q.where(or_(ServiceNote.title.ilike(like), ServiceNote.body.ilike(like)))
            rows = (await db.execute(q.limit(3))).scalars().all()
            return {"notes": [
                {"title": n.title, "snippet": n.body[:500], "source": n.source} for n in rows]}

        return _run(_impl)


def build_tools(orgs: Optional[list[str]] = None) -> list:
    """All copilot tools bound to one tenant. READ-ONLY by construction."""
    return [
        FleetStatusTool(orgs=orgs),
        DeviceLookupTool(orgs=orgs),
        DtcLookupTool(orgs=orgs),
        TelemetryTool(orgs=orgs),
        AlertSearchTool(orgs=orgs),
        CargoStatusTool(orgs=orgs),
        NotesSearchTool(orgs=orgs),
    ]
