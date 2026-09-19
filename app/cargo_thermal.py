"""SRS Idea 4 M1.2: cold-chain Time-to-Spoilage estimator + cargo anomaly checks.

v1 is a documented heuristic (same honesty grade as legacy slopes):
linear extrapolation of the 15-min bay-temp slope against the profile
threshold, scaled by thermal mass, with a door-open penalty. Median smoothing
over the last 3 readings kills single-sample spikes (SRS failure mode).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CargoProfile, CargoReading
from app.utils import utcnow

logger = logging.getLogger(__name__)

WINDOW_MINUTES = 15
SMOOTH_N = 3
DOOR_PENALTY_MIN = 30.0  # door open drags TTS down (warm air ingress)


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def estimate_tts(readings: list, temp_max_c: float, thermal_mass: float = 1.0) -> dict:
    """Estimate minutes until bay temp crosses temp_max_c.

    readings: ascending (timestamp, bay_temp_c) pairs (already smoothed input
    preferred). Returns {tts_minutes|None, risk_level, slope_c_per_min}.
    """
    pts = [(ts, t) for ts, t in readings if t is not None]
    if len(pts) < 2:
        return {"tts_minutes": None, "risk_level": "UNKNOWN", "slope_c_per_min": 0.0}
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    dt_min = max((t1 - t0).total_seconds() / 60.0, 0.5)
    slope = (v1 - v0) / dt_min / max(thermal_mass, 0.1)
    current = _median([v for _, v in pts[-SMOOTH_N:]])
    if slope <= 0:
        # Cooling or flat: breach only if already over threshold.
        if current > temp_max_c:
            return {"tts_minutes": 0.0, "risk_level": "HIGH", "slope_c_per_min": round(slope, 4)}
        return {"tts_minutes": None, "risk_level": "LOW", "slope_c_per_min": round(slope, 4)}
    tts = (temp_max_c - current) / slope
    if tts < 0:
        return {"tts_minutes": 0.0, "risk_level": "HIGH", "slope_c_per_min": round(slope, 4)}
    level = "HIGH" if tts < 60 else ("MEDIUM" if tts < 180 else "LOW")
    return {"tts_minutes": round(tts, 1), "risk_level": level, "slope_c_per_min": round(slope, 4)}


async def assess_cargo_device(db: AsyncSession, device_id: str, device_name: str = "") -> list[dict]:
    """Build cargo anomaly dicts (AlertEngine contract) for one device."""
    anomalies = []
    prof = (
        await db.execute(select(CargoProfile).where(CargoProfile.device_id == device_id))
    ).scalar_one_or_none()
    if not prof:
        return anomalies
    cutoff = utcnow() - timedelta(minutes=WINDOW_MINUTES * 2)
    rows = (
        await db.execute(
            select(CargoReading)
            .where(CargoReading.device_id == device_id, CargoReading.timestamp >= cutoff)
            .order_by(CargoReading.timestamp.asc())
            .limit(60)
        )
    ).scalars().all()
    if len(rows) < 2:
        return anomalies
    est = estimate_tts(
        [(r.timestamp, r.bay_temp_c) for r in rows],
        prof.temp_max_c, prof.thermal_mass or 1.0,
    )
    tts = est["tts_minutes"]
    door_open = any(r.door_open for r in rows[-SMOOTH_N:])
    if tts is not None and prof.door_alerts and door_open:
        tts = max(0.0, tts - DOOR_PENALTY_MIN)
        est["risk_level"] = "HIGH" if tts < 60 else est["risk_level"]
    label = device_name or device_id[:8]
    if tts is not None and tts < 180:
        severity = "critical" if tts < 60 else "warning"
        trip_note = ""
        if prof.trip_eta_minutes and tts < prof.trip_eta_minutes:
            trip_note = (
                f" Predicted breach {tts:.0f} min BEFORE trip end "
                f"({prof.trip_eta_minutes:.0f} min) — reroute to refrigerated "
                f"depot or prioritize this delivery."
            )
        anomalies.append({
            "type": "cargo_spoilage_risk",
            "severity": severity,
            "message": (
                f"Cargo on '{label}' breaching {prof.temp_max_c}°C in ~{tts:.0f} min "
                f"(bay {rows[-1].bay_temp_c}°C, slope {est['slope_c_per_min']}°C/min)."
                + trip_note
            ),
            "affected_device_ids": [device_id],
            "timestamp": utcnow().isoformat(),
        })
    if prof.door_alerts and door_open:
        anomalies.append({
            "type": "cargo_door_open",
            "severity": "warning",
            "message": f"Cargo door open on '{label}' (cold-chain breach risk).",
            "affected_device_ids": [device_id],
            "timestamp": utcnow().isoformat(),
        })
    return anomalies


async def assess_cargo_fleet(db: AsyncSession) -> list[dict]:
    """Assess every device carrying a cargo profile."""
    from app.models import Device

    out = []
    profs = (await db.execute(select(CargoProfile))).scalars().all()
    names = {}
    if profs:
        devs = (
            await db.execute(
                select(Device).where(Device.id.in_([p.device_id for p in profs]))
            )
        ).scalars().all()
        names = {d.id: d.name for d in devs}
    for p in profs:
        try:
            out.extend(await assess_cargo_device(db, p.device_id, names.get(p.device_id, "")))
        except Exception:
            logger.exception("Cargo assessment failed for %s", p.device_id)
    return out
