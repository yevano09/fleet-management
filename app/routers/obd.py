"""
Fleet Commander — OBD-II helpers API (P0-A).

Read-only DTC decode table for dashboard tooltips, agent messages and
runbooks. Live OBD/BMS ingest arrives over MQTT, not here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import require_user
from app.obd.dtc import KNOWN, describe_dtc

router = APIRouter(prefix="/obd", tags=["obd"])


@router.get("/dtc/{code}")
async def decode_dtc(code: str, principal: dict = Depends(require_user())):
    """Human meaning for one diagnostic trouble code."""
    return {"code": code.strip().upper(), "meaning": describe_dtc(code)}


@router.get("/dtc")
async def list_known_dtcs(principal: dict = Depends(require_user())):
    """Curated DTC table (codes this fleet emits or handles)."""
    return {"dtcs": [{"code": k, "meaning": v} for k, v in sorted(KNOWN.items())]}
