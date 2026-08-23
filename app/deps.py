"""Fleet Commander — P0 shared auth dependencies (UC-23 + UC-26).

Single source of truth for:
  - principal resolution (open-mode bypass, JWT cookie/bearer, X-API-Key)
  - require_user / require_role / require_admin FastAPI dependencies
  - organization scoping helper for tenant queries

Role matrix (enforced, nothing looser) — see SECURITY.md § RBAC.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import COOKIE_NAME, ADMIN_COOKIE_NAME, decode_jwt_token, is_session_revoked
from app.config import settings, DEFAULT_ORG_ID, SUPER_ORG
from app.database import get_db
from app.models import ApiKey

logger = logging.getLogger(__name__)

# P0 UC-23 RBAC hierarchy. Roles are ORDERED minimums:
#   viewer < user < operator < fleet_manager < admin
# require_role("operator") therefore admits operator, fleet_manager AND admin.
ROLE_RANK = {
    "viewer": 0,
    "user": 1,
    "operator": 2,
    "fleet_manager": 3,
    "admin": 4,
}

# Synthetic principal used when AUTH_MODE=open so that audit actors and
# org scoping keep working unchanged in the legacy local/demo profile.
OPEN_PRINCIPAL = {
    "email": "open@local",
    "name": "open-mode",
    "role": "admin",
    "org_id": SUPER_ORG,
    "auth": "open",
}


async def _api_key_lookup(db: AsyncSession, raw_key: str) -> Optional[ApiKey]:
    """Hash-lookup an API key. Kept as a tiny function so unit tests can
    monkeypatch it without touching the database."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.revoked == 0)
    )
    return result.scalar_one_or_none()


async def resolve_principal(request: Request, db: AsyncSession) -> Optional[dict]:
    """Resolve the authenticated principal or None.

    Priority: X-API-Key header → Authorization: Bearer JWT → session cookies.
    In AUTH_MODE=open every request gets the synthetic admin principal.
    """
    if settings.auth_mode == "open":
        return dict(OPEN_PRINCIPAL)

    # 1. API key (automation path — E2E tests, agents/tools.py)
    raw_key = request.headers.get("X-API-Key")
    if raw_key:
        row = await _api_key_lookup(db, raw_key)
        if not row:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return {
            "email": f"apikey:{row.name}",
            "name": f"apikey:{row.name}",
            "role": row.role.value if hasattr(row.role, "value") else str(row.role),
            "org_id": row.org_id or DEFAULT_ORG_ID,
            "auth": "api_key",
        }

    # 2. Bearer token (Authorization header wins over cookies)
    token: Optional[str] = None
    authz = request.headers.get("Authorization", "")
    if authz.startswith("Bearer "):
        token = authz[len("Bearer "):].strip()
    token = token or request.cookies.get(COOKIE_NAME) or request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        return None

    payload = decode_jwt_token(token)
    if not payload:
        return None

    role = payload.get("role", "")
    if role == "admin":
        return {
            "email": f"admin:{payload.get('username', 'admin')}",
            "name": payload.get("username", "admin"),
            "role": "admin",
            "org_id": payload.get("org_id", SUPER_ORG),
            "auth": "jwt",
        }

    session_id = payload.get("session_id")
    if session_id and await is_session_revoked(session_id):
        return None

    return {
        "email": payload.get("email") or "unknown@user",
        "name": payload.get("name") or (payload.get("email") or "user"),
        "role": role or "viewer",
        "org_id": payload.get("org_id") or DEFAULT_ORG_ID,
        "auth": "jwt",
    }


def require_user():
    """401 unless a valid principal is present (any role)."""

    async def dep(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
        principal = await resolve_principal(request, db)
        if not principal:
            raise HTTPException(status_code=401, detail="Authentication required")
        return principal

    return dep


def require_role(*minimums: str):
    """401 unauthenticated; 403 below the minimum rank.

    Each argument is a MINIMUM level; the check passes if the principal's
    rank >= ANY listed minimum. `admin` (rank 4) satisfies every minimum.
    Unknown role strings fail closed.
    """

    thresholds = [ROLE_RANK.get(m) for m in minimums]
    if not thresholds or any(t is None for t in thresholds):
        raise ValueError(f"require_role() got unknown level(s): {minimums}")
    min_rank = min(t for t in thresholds)

    async def dep(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
        principal = await resolve_principal(request, db)
        if not principal:
            raise HTTPException(status_code=401, detail="Authentication required")
        role = principal.get("role", "")
        rank = ROLE_RANK.get(role)
        if rank is None or rank < min_rank:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role or 'none'}' is not permitted for this operation "
                       f"(requires {min(minimums)}+)",
            )
        return principal

    return dep


def require_admin():
    """Only admins (incl. API keys minted with role=admin) pass."""
    async def dep(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
        principal = await resolve_principal(request, db)
        if not principal:
            raise HTTPException(status_code=401, detail="Authentication required")
        if principal.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Administrator role required")
        return principal
    return dep


def allowed_orgs(principal: dict) -> Optional[list[str]]:
    """Organization scope for tenant queries.

    Returns:
        None          → unrestricted (super-admin, org_id claim '*')
        list[str]     → concrete org ids the caller may touch
    """
    if principal.get("role") == "admin" and principal.get("org_id") == SUPER_ORG:
        return None
    org_id = principal.get("org_id") or DEFAULT_ORG_ID
    if org_id == SUPER_ORG:  # defensive: non-admin can never hold '*'
        return [DEFAULT_ORG_ID]
    return [org_id]


def scope_devices(query, principal: dict):
    """Apply Device.org_id tenancy filter to a query selecting Device rows."""
    orgs = allowed_orgs(principal)
    from app.models import Device

    if orgs is not None:
        query = query.where(Device.org_id.in_(orgs))
    return query
