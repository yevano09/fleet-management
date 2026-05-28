import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from httpx import AsyncClient

from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

COOKIE_NAME = "fleet_token"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_jwt_token(email: str, name: str, picture: str) -> str:
    payload = {
        "email": email,
        "name": name,
        "picture": picture,
        "iat": _utcnow(),
        "exp": _utcnow() + timedelta(minutes=settings.jwt_expiration_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid JWT token: %s", e)
        return None


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


def set_auth_cookie(response, token: str):
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.jwt_expiration_minutes * 60,
        secure=False,
    )


def clear_auth_cookie(response):
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return decode_jwt_token(token)


async def require_auth(request: Request):
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def optional_auth(request: Request) -> dict:
    user = get_current_user(request)
    return user or {}
