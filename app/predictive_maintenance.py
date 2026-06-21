"""
Fleet Commander — Predictive Maintenance Engine (Feature 3)

Analyzes telemetry trends to predict device failures before they happen.
Uses linear-regression slope analysis on signal degradation, rising
temperature, declining SOH, and intermittent connectivity patterns.
"""

from __future__ import annotations

import logging
from typing import Optional
from datetime import timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Telemetry, PredictedFailure, Device, DeviceStatus
from app.utils import utcnow
from app.metrics import predicted_failures_total, predicted_failures_active

logger = logging.getLogger(__name__)

# Minimum data points required to compute a trend
MIN_POINTS = 5
# Risk score thresholds
RISK_HIGH = 0.7
RISK_MEDIUM = 0.4


def _linear_slope(values: list[float]) -> float:
    """Simple least-squares slope of a value series (y vs index)."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den != 0 else 0.0


def _hours_to_threshold(current: float, slope_per_step: float, threshold: float, step_hours: float) -> Optional[float]:
    """Estimate hours until current value crosses threshold given a slope."""
    if slope_per_step == 0:
        return None
    steps = (threshold - current) / slope_per_step
    if steps <= 0:
        return None
    return steps * step_hours


async def analyze_device(db: AsyncSession, device: Device, lookback_hours: int = 24) -> Optional[PredictedFailure]:
    """Analyze a single device's telemetry trends and create a prediction if risk found."""
    cutoff = utcnow() - timedelta(hours=lookback_hours)
    result = await db.execute(
        select(Telemetry)
        .where(Telemetry.device_id == device.id, Telemetry.timestamp >= cutoff)
        .order_by(Telemetry.timestamp.asc())
    )
    points = result.scalars().all()

    if len(points) < MIN_POINTS:
        return None

    step_hours = lookback_hours / max(len(points), 1)
    predictions = []

    # ── Signal degradation trend ──
    signals = [p.signal_strength for p in points if p.signal_strength is not None]
    if len(signals) >= MIN_POINTS:
        slope = _linear_slope([float(s) for s in signals])
        if slope < -0.5:  # signal getting weaker over time
            current_sig = float(signals[-1])
            htf = _hours_to_threshold(current_sig, slope, -100.0, step_hours)
            risk = min(1.0, abs(slope) * 10)
            if risk >= RISK_MEDIUM:
                predictions.append({
                    "risk_type": "signal_degradation",
                    "risk_score": round(risk, 3),
                    "confidence": min(1.0, len(signals) / 20),
                    "predicted_hours_to_failure": htf,
                    "evidence": {"current_signal": current_sig, "slope": round(slope, 4), "samples": len(signals)},
                    "recommendation": "Inspect antenna placement and connectivity. Signal trending downward.",
                })

    # ── Thermal trend (rising temperature) ──
    temps = [p.temperature for p in points if p.temperature is not None]
    if len(temps) >= MIN_POINTS:
        slope = _linear_slope([float(t) for t in temps])
        current_temp = float(temps[-1])
        if slope > 0.3 or current_temp > 70:
            htf = _hours_to_threshold(current_temp, slope, 85.0, step_hours)
            risk = min(1.0, (slope * 5) + (0.5 if current_temp > 70 else 0))
            if risk >= RISK_MEDIUM:
                predictions.append({
                    "risk_type": "thermal",
                    "risk_score": round(risk, 3),
                    "confidence": min(1.0, len(temps) / 20),
                    "predicted_hours_to_failure": htf,
                    "evidence": {"current_temp": current_temp, "slope": round(slope, 4), "samples": len(temps)},
                    "recommendation": "Check cooling/ventilation. Temperature trending upward.",
                })

    # Battery degradation (EV devices)
    sohs = [p.soh for p in points if p.soh is not None]
    if len(sohs) >= MIN_POINTS:
        slope = _linear_slope([float(s) for s in sohs])
        current_soh = float(sohs[-1])
        if slope < -0.05 or current_soh < 75:
            htf = _hours_to_threshold(current_soh, slope, 60.0, step_hours)
            risk = min(1.0, abs(slope) * 50 + (0.3 if current_soh < 75 else 0))
            if risk >= RISK_MEDIUM:
                predictions.append({
                    "risk_type": "battery_degradation",
                    "risk_score": round(risk, 3),
                    "confidence": min(1.0, len(sohs) / 20),
                    "predicted_hours_to_failure": htf,
                    "evidence": {"current_soh": current_soh, "slope": round(slope, 4), "samples": len(sohs)},
                    "recommendation": "Schedule battery health check. SOH declining.",
                })

    # Intermittent connectivity (uptime fluctuations)
    uptimes = [p.uptime_percentage for p in points if p.uptime_percentage is not None]
    if len(uptimes) >= MIN_POINTS:
        low_count = sum(1 for u in uptimes if float(u) < 95.0)
        if low_count >= len(uptimes) * 0.3:
            risk = min(1.0, low_count / len(uptimes))
            predictions.append({
                "risk_type": "intermittent",
                "risk_score": round(risk, 3),
                "confidence": min(1.0, len(uptimes) / 20),
                "predicted_hours_to_failure": None,
                "evidence": {"low_uptime_samples": low_count, "total_samples": len(uptimes), "avg_uptime": round(sum(float(u) for u in uptimes) / len(uptimes), 2)},
                "recommendation": "Device showing intermittent connectivity. Check power supply and network.",
            })

    if not predictions:
        return None

    # Pick the highest-risk prediction
    best = max(predictions, key=lambda p: p["risk_score"])
    import json
    pred = PredictedFailure(
        device_id=device.id,
        risk_type=best["risk_type"],
        risk_score=best["risk_score"],
        confidence=best["confidence"],
        predicted_hours_to_failure=best["predicted_hours_to_failure"],
        evidence=json.dumps(best["evidence"]),
        recommendation=best["recommendation"],
        created_at=utcnow(),
    )
    db.add(pred)
    await db.commit()
    await db.refresh(pred)
    predicted_failures_total.labels(risk_type=best["risk_type"]).inc()
    predicted_failures_active.inc()
    logger.info("Predicted failure for device %s: %s (risk=%.2f)", device.name, best["risk_type"], best["risk_score"])
    return pred


async def run_prediction_cycle(db: AsyncSession, lookback_hours: int = 24) -> list[PredictedFailure]:
    """Run predictive analysis across all online devices."""
    result = await db.execute(select(Device).where(Device.status == DeviceStatus.online))
    devices = result.scalars().all()
    predictions = []
    for device in devices:
        try:
            pred = await analyze_device(db, device, lookback_hours)
            if pred:
                predictions.append(pred)
        except Exception:
            logger.exception("Prediction failed for device %s", device.id)
    return predictions


async def get_predictions(
    db: AsyncSession,
    device_id: Optional[str] = None,
    resolved: Optional[bool] = False,
    min_risk: float = 0.0,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Fetch predictions with filtering."""
    query = select(PredictedFailure)
    count_query = select(func.count()).select_from(PredictedFailure)
    if device_id:
        query = query.where(PredictedFailure.device_id == device_id)
        count_query = count_query.where(PredictedFailure.device_id == device_id)
    if resolved is not None:
        query = query.where(PredictedFailure.resolved == resolved)
        count_query = count_query.where(PredictedFailure.resolved == resolved)
    if min_risk > 0:
        query = query.where(PredictedFailure.risk_score >= min_risk)
        count_query = count_query.where(PredictedFailure.risk_score >= min_risk)
    query = query.order_by(PredictedFailure.risk_score.desc())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    result = await db.execute(query.offset(offset).limit(limit))
    preds = result.scalars().all()
    return {"predictions": preds, "total": total}
