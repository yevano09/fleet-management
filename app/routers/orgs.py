"""Fleet Commander — Organization API (P0 UC-26)."""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_admin, require_user, allowed_orgs
from app.models import Organization, DEFAULT_ORG_ID
from app.audit import log_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orgs", tags=["organizations"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")


class OrgCreateRequest(BaseModel):
    name: str
    slug: str | None = None


class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str

    model_config = {"from_attributes": True}


class OrgListResponse(BaseModel):
    orgs: list[OrgResponse]
    total: int


@router.post("", response_model=OrgResponse, status_code=201)
async def create_org(
    req: OrgCreateRequest,
    principal: dict = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    slug = (req.slug or req.name.lower().replace(" ", "-")).strip()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="slug must match ^[a-z0-9][a-z0-9-]{0,48}$")

    existing = await db.execute(select(Organization).where(Organization.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Org slug '{slug}' already exists")

    org = Organization(id=str(uuid.uuid4()), name=req.name, slug=slug)
    db.add(org)
    await db.commit()
    await db.refresh(org)
    await log_action(db, principal["email"], "org.create", "org", org.id, {"name": req.name})
    return OrgResponse.model_validate(org)


@router.get("", response_model=OrgListResponse)
async def list_orgs(
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    query = select(Organization).order_by(Organization.created_at.asc())
    orgs_scope = allowed_orgs(principal)
    if orgs_scope is not None:
        ids = orgs_scope if orgs_scope else [DEFAULT_ORG_ID]
        query = query.where(Organization.id.in_(ids))
    result = await db.execute(query)
    orgs = result.scalars().all()
    return OrgListResponse(orgs=[OrgResponse.model_validate(o) for o in orgs], total=len(orgs))


@router.get("/mine", response_model=list[OrgResponse])
async def my_orgs(
    principal: dict = Depends(require_user()),
    db: AsyncSession = Depends(get_db),
):
    scope = allowed_orgs(principal)
    ids = scope if scope is not None else None
    query = select(Organization)
    if ids:
        query = query.where(Organization.id.in_(ids))
    result = await db.execute(query.order_by(Organization.name.asc()))
    return [OrgResponse.model_validate(o) for o in result.scalars().all()]
