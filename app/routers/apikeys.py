"""Fleet Commander — API key management (P0 UC-23).

Admin-only CRUD for automation tokens. The raw secret (`fck_...`) is shown
exactly once at creation; only its SHA-256 hash is persisted.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DEFAULT_ORG_ID
from app.database import get_db
from app.deps import require_admin, allowed_orgs
from app.models import ApiKey, UserRole
from app.audit import log_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api-keys", tags=["admin-api-keys"])

KEY_PREFIX = "fck_"


class ApiKeyCreateRequest(BaseModel):
    name: str
    role: str = "viewer"
    org_id: str = DEFAULT_ORG_ID


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    prefix: str
    role: str
    org_id: str
    revoked: bool

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(ApiKeyResponse):
    secret: str  # returned exactly once


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    req: ApiKeyCreateRequest,
    principal: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    try:
        role_enum = UserRole(req.role)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of {[r.value for r in UserRole]}",
        )

    # Non-super admins may only mint keys inside their own org scope.
    scope = allowed_orgs(principal)
    if scope is not None and req.org_id not in scope:
        raise HTTPException(status_code=404, detail="Organization not found")

    secret = KEY_PREFIX + secrets.token_hex(24)
    row = ApiKey(
        id=str(uuid.uuid4()),
        org_id=req.org_id,
        name=req.name,
        prefix=secret[:12],
        key_hash=_hash(secret),
        role=role_enum if role_enum != UserRole.admin else UserRole.fleet_manager,
        # API keys are never minted as super-admin; admin role collapses to
        # fleet_manager to keep '*' scope out of automation tokens.
        revoked=0,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await log_action(db, principal["email"], "apikey.create", "api_key", row.id,
                     {"name": req.name, "role": req.role, "org_id": req.org_id})
    resp = ApiKeyCreatedResponse(
        id=row.id, name=row.name, prefix=row.prefix,
        role=(row.role.value if hasattr(row.role, "value") else str(row.role)),
        org_id=row.org_id, revoked=bool(row.revoked), secret=secret,
    )
    return resp


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    principal: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return [ApiKeyResponse.model_validate(k) for k in result.scalars().all()]


@router.post("/{key_id}/revoke")
async def revoke_api_key(
    key_id: str,
    principal: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    row.revoked = 1
    await db.commit()
    await log_action(db, principal["email"], "apikey.revoke", "api_key", key_id)
    return {"message": "API key revoked", "key_id": key_id}
