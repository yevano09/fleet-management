"""SRS Idea 4 M1.3: handling-event classifier (heuristic v1).

Classifies IMU peak-g observations into NORMAL_ROAD_BUMP | CORNERING_FORCE |
HARD_DROP | CARGO_COLLISION. v1 is thresholds + duration (documented
stand-in, same honesty grade as legacy slopes); the edge contract
(topic + payload shape + model_version) is what future TFLite models must
speak, so promotion never changes the wire format.

Candidate-dict contract mirrors ml_score_points: callers pick/persist.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CargoReading, ShockEvent
from app.utils import utcnow

logger = logging.getLogger(__name__)

MODEL_VERSION = "heuristic-v1"

# Peak-g bands (SRS: >=100 Hz IMU, 500 ms windows; simulator emits per-beat peaks).
HARD_DROP_G = 5.0
COLLISION_G = 8.0
BUMP_G = 2.0


def classify_shock(peak_g: float, axis: str = "z", duration_ms: float = 500.0) -> dict:
    """Classify one shock observation. Pure function (eval-friendly)."""
    if peak_g >= COLLISION_G:
        cls, risk = "CARGO_COLLISION", 1.0
    elif peak_g >= HARD_DROP_G:
        cls, risk = "HARD_DROP", 0.85
    elif peak_g >= BUMP_G and duration_ms > 800:
        cls, risk = "CORNERING_FORCE", 0.5
    else:
        cls, risk = "NORMAL_ROAD_BUMP", 0.1
    return {
        "event_class": cls,
        "risk_score": risk,
        "evidence": {"peak_g": peak_g, "axis": axis, "duration_ms": duration_ms},
        "model_version": MODEL_VERSION,
    }


async def scan_shocks(db: AsyncSession, lookback_hours: int = 1) -> list[ShockEvent]:
    """Classify recent cargo readings' shock peaks into ShockEvent rows.

    Skips readings already covered (re-run safe via timestamp watermark per
    device handled implicitly: only peaks >= BUMP_G create rows, and exact
    (device, timestamp) dupes are skipped).
    """
    cutoff = utcnow() - timedelta(hours=lookback_hours)
    rows = (
        await db.execute(
            select(CargoReading)
            .where(CargoReading.timestamp >= cutoff, CargoReading.shock_g.isnot(None))
            .order_by(CargoReading.timestamp.asc())
            .limit(2000)
        )
    ).scalars().all()
    created = []
    for r in rows:
        if (r.shock_g or 0) < BUMP_G:
            continue
        exists = await db.execute(
            select(ShockEvent).where(
                ShockEvent.device_id == r.device_id,
                ShockEvent.timestamp == r.timestamp,
            )
        )
        if exists.scalar_one_or_none():
            continue
        verdict = classify_shock(r.shock_g)
        if verdict["event_class"] == "NORMAL_ROAD_BUMP":
            continue  # store only actionable classes
        ev = ShockEvent(
            device_id=r.device_id,
            timestamp=r.timestamp,
            peak_g=r.shock_g,
            axis="vector",
            event_class=verdict["event_class"],
            model_version=verdict["model_version"],
        )
        # org from device would need a join; readers scope by device → org via devices.
        from app.models import Device

        dev = await db.execute(select(Device).where(Device.id == r.device_id))
        device = dev.scalar_one_or_none()
        ev.org_id = device.org_id if device else "org-default"
        db.add(ev)
        created.append(ev)
    if created:
        await db.commit()
        from app.metrics import shock_events_total

        for ev in created:
            shock_events_total.labels(event_class=ev.event_class).inc()
    return created
