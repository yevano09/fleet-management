import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Request, HTTPException
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings, DEFAULT_ORG_ID, SUPER_ORG
from app.database import async_session_factory
from app.models import UserSession
from app.utils import utcnow

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

COOKIE_NAME = "fleet_token"
ADMIN_COOKIE_NAME = "fleet_admin_token"


# ── JWT helpers ──────────────────────────────────────────────────────────

def create_user_jwt_token(
    email: str,
    name: str,
    picture: str,
    session_id: str,
    role: str = "user",
    org_id: str = DEFAULT_ORG_ID,
) -> str:
    payload = {
        "email": email,
        "name": name,
        "picture": picture,
        "session_id": session_id,
        "role": role,
        "org_id": org_id,  # P0 UC-26: tenancy claim (UC-23: role claim)
        "iat": utcnow(),
        "exp": utcnow() + timedelta(minutes=settings.jwt_expiration_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_admin_jwt_token(username: str, org_id: str = SUPER_ORG) -> str:
    payload = {
        "username": username,
        "role": "admin",
        "org_id": org_id,  # "*" = super-admin over all orgs (UC-26)
        "iat": utcnow(),
        "exp": utcnow() + timedelta(minutes=settings.jwt_expiration_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid JWT token: %s", e)
        return None


# ── Session tracking ─────────────────────────────────────────────────────

async def create_session(email: str, name: str, picture: str,
                         role: str = None, org_id: str = DEFAULT_ORG_ID) -> str:
    from app.models import UserRole

    session_id = str(uuid.uuid4())
    resolved_role = role or settings.default_user_role
    try:
        role_enum = UserRole(resolved_role)
    except ValueError:
        role_enum = UserRole.viewer
    async with async_session_factory() as db:
        session = UserSession(
            id=session_id,
            email=email,
            name=name,
            picture=picture,
            login_time=utcnow(),
            last_active=utcnow(),
            revoked=0,
            role=role_enum,
            org_id=org_id,
        )
        db.add(session)
        await db.commit()
    return session_id


async def revoke_session(session_id: str) -> bool:
    async with async_session_factory() as db:
        result = await db.execute(select(UserSession).where(UserSession.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return False
        session.revoked = 1
        await db.commit()
        return True


async def is_session_revoked(session_id: str) -> bool:
    async with async_session_factory() as db:
        result = await db.execute(select(UserSession).where(UserSession.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return True
        return session.revoked == 1


async def get_active_sessions() -> list[dict]:
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserSession).where(UserSession.revoked == 0).order_by(UserSession.login_time.desc())
        )
        sessions = result.scalars().all()
        return [
            {
                "id": s.id,
                "email": s.email,
                "name": s.name,
                "picture": s.picture,
                "login_time": s.login_time.isoformat() if s.login_time else "",
                "last_active": s.last_active.isoformat() if s.last_active else "",
            }
            for s in sessions
        ]


async def get_all_sessions() -> list[dict]:
    async with async_session_factory() as db:
        result = await db.execute(
            select(UserSession).order_by(UserSession.login_time.desc())
        )
        sessions = result.scalars().all()
        return [
            {
                "id": s.id,
                "email": s.email,
                "name": s.name,
                "picture": s.picture,
                "login_time": s.login_time.isoformat() if s.login_time else "",
                "last_active": s.last_active.isoformat() if s.last_active else "",
                "revoked": s.revoked == 1,
            }
            for s in sessions
        ]


# ── Google OAuth helpers ─────────────────────────────────────────────────

def get_google_redirect_uri() -> str:
    return settings.google_redirect_uri


def get_google_auth_url() -> str:
    params = (
        f"client_id={settings.google_client_id}"
        f"&redirect_uri={get_google_redirect_uri()}"
        f"&response_type=code"
        f"&scope=openid%20email%20profile"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    return f"{GOOGLE_AUTH_URL}?{params}"


async def exchange_code_for_token(code: str) -> Optional[dict]:
    data = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": get_google_redirect_uri(),
        "grant_type": "authorization_code",
    }
    async with AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data=data)
        if resp.status_code != 200:
            logger.error("Google token exchange failed: %s", resp.text)
            return None
        return resp.json()


async def get_google_user_info(access_token: str) -> Optional[dict]:
    async with AsyncClient() as client:
        resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            logger.error("Google userinfo failed: %s", resp.text)
            return None
        return resp.json()


# ── Cookie helpers ───────────────────────────────────────────────────────

def set_auth_cookie(response, token: str):
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.jwt_expiration_minutes * 60,
        secure=settings.secure_cookies,
    )


def clear_auth_cookie(response):
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


def set_admin_cookie(response, token: str):
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.jwt_expiration_minutes * 60,
        secure=settings.secure_cookies,
    )


def clear_admin_cookie(response):
    response.delete_cookie(
        key=ADMIN_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


# ── Request helpers ──────────────────────────────────────────────────────

async def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = decode_jwt_token(token)
    if not payload or payload.get("role") != "user":
        return None
    session_id = payload.get("session_id")
    if session_id and await is_session_revoked(session_id):
        return None
    return payload


def get_current_admin(request: Request) -> Optional[dict]:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        return None
    payload = decode_jwt_token(token)
    if not payload or payload.get("role") != "admin":
        return None
    return payload
