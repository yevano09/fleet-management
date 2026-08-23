"""Fleet Commander — Device certificate lifecycle API (P0 UC-25).

Issue / rotate / revoke / list device certificates from the internal CA.
Private keys leave the system exactly once, in the issue/rotate response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role, require_user, allowed_orgs
from app.models import Device, DeviceCertificate
from app.pki import issue_device_cert, crypto_available, refresh_crl_from_db
from app.audit import log_action
from app.utils import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["certificates"])


class CertIssuedResponse(BaseModel):
    device_id: str | None = None
    fingerprint_sha256: str
    serial: str
    expires_at: str | None = None
    cert_pem: str
    key_pem: str  # one-time — never persisted


async def _device_in_scope(db: AsyncSession, device_id: str, principal: dict) -> Device:
    query = select(Device).where(Device.id == device_id)
    orgs = allowed_orgs(principal)
    if orgs is not None:
        query = query.where(Device.org_id.in_(orgs))
    result = await db.execute(query)
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def _issued_response(row: DeviceCertificate, key_pem: str) -> CertIssuedResponse:
    return CertIssuedResponse(
        device_id=row.device_id,
        fingerprint_sha256=row.fingerprint_sha256,
        serial=row.serial,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        cert_pem=row.pem,
        key_pem=key_pem,
    )


@router.post("/devices/{device_id}/certs", response_model=CertIssuedResponse)
async def issue_cert(
    device_id: str,
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    """Issue a certificate for a device identity (CN = device_id).

    Works for BOTH flows:
      - existing device → cert bound to that device's org;
      - never-seen identity (pre-issue for physical bring-up) → the cert row
        is created with the CALLER's org so that JITP (first verified
        `iot/fleet/{cn}/register`) provisions the device into that org.
    The private key is returned exactly once and never persisted.
    """
    if not crypto_available():
        raise HTTPException(status_code=503, detail="PKI unavailable (cryptography missing)")

    query = select(Device).where(Device.id == device_id)
    scope = allowed_orgs(principal)
    if scope is not None:
        query = query.where(Device.org_id.in_(scope))
    device = (await db.execute(query)).scalar_one_or_none()

    if device is not None:
        cert_org = device.org_id or "org-default"
    else:
        # Pre-issue (UC-25 JITP): identity not onboarded yet.
        cert_org = scope[0] if scope else "org-default"

    issued = issue_device_cert(device_id=device_id, org_id=cert_org)
    row = DeviceCertificate(
        device_id=device_id,
        org_id=cert_org,
        fingerprint_sha256=issued["fingerprint_sha256"],
        pem=issued["cert_pem"],
        serial=issued["serial"],
        status="active",
        issued_at=utcnow(),
        expires_at=issued["expires_at"],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await log_action(db, principal["email"], "cert.issue", "certificate",
                     row.fingerprint_sha256[:16],
                     {"device_id": device_id, "pre_issued": device is None})
    return _issued_response(row, issued["key_pem"])


@router.post("/devices/{device_id}/certs/rotate", response_model=CertIssuedResponse)
async def rotate_cert(
    device_id: str,
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the device's active certificate(s), then issue a fresh pair."""
    if not crypto_available():
        raise HTTPException(status_code=503, detail="PKI unavailable (cryptography missing)")
    device = await _device_in_scope(db, device_id, principal)

    result = await db.execute(
        select(DeviceCertificate).where(
            DeviceCertificate.device_id == device.id,
            DeviceCertificate.status.in_(("active", "issued")),
        )
    )
    for old in result.scalars().all():
        old.status = "revoked"
        old.revoked_at = utcnow()

    issued = issue_device_cert(device_id=device.id, org_id=device.org_id or "org-default")
    row = DeviceCertificate(
        device_id=device.id,
        org_id=device.org_id or "org-default",
        fingerprint_sha256=issued["fingerprint_sha256"],
        pem=issued["cert_pem"],
        serial=issued["serial"],
        status="active",
        issued_at=utcnow(),
        expires_at=issued["expires_at"],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await refresh_crl_from_db(db)
    await log_action(db, principal["email"], "cert.rotate", "certificate",
                     row.fingerprint_sha256[:16], {"device_id": device.id})
    return _issued_response(row, issued["key_pem"])


@router.post("/certs/{fingerprint}/revoke")
async def revoke_cert(
    fingerprint: str,
    principal: dict = Depends(require_role("fleet_manager")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeviceCertificate).where(DeviceCertificate.fingerprint_sha256 == fingerprint)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if row.status == "revoked":
        return {"message": "Certificate already revoked", "fingerprint": fingerprint}

    # Cross-org check: non-super callers may only revoke within their scope.
    scope = allowed_orgs(principal)
    if scope is not None and (row.org_id not in scope):
        raise HTTPException(status_code=404, detail="Certificate not found")

    row.status = "revoked"
    row.revoked_at = utcnow()
    await db.commit()
    crl_file = await refresh_crl_from_db(db)
    await log_action(db, principal["email"], "cert.revoke", "certificate", fingerprint[:16])
    return {
        "message": "Certificate revoked; CRL regenerated (restart mosquitto-tls to enforce)",
        "fingerprint": fingerprint,
        "crl_file": crl_file,
    }


@router.get("/devices/{device_id}/certs")
async def list_certs(
    device_id: str,
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    await _device_in_scope(db, device_id, principal)
    result = await db.execute(
        select(DeviceCertificate)
        .where(DeviceCertificate.device_id == device_id)
        .order_by(DeviceCertificate.issued_at.desc())
    )
    return [
        {
            "fingerprint_sha256": c.fingerprint_sha256,
            "serial": c.serial,
            "status": c.status,
            "issued_at": c.issued_at.isoformat() if c.issued_at else None,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
        }
        for c in result.scalars().all()
    ]
