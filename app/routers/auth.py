import logging

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import os

from app.auth import (
    get_google_auth_url,
    exchange_code_for_token,
    get_google_user_info,
    create_jwt_token,
    set_auth_cookie,
    clear_auth_cookie,
    require_auth,
    optional_auth,
    COOKIE_NAME,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


@router.get("/login")
async def login():
    return RedirectResponse(url=get_google_auth_url())


@router.get("/callback")
async def callback(code: str = None, error: str = None, request: Request = None):
    if error:
        logger.error("Google OAuth error: %s", error)
        return HTMLResponse(content=f"<h1>Authentication failed</h1><p>{error}</p>", status_code=400)

    if not code:
        return HTMLResponse(content="<h1>Missing authorization code</h1>", status_code=400)

    token_data = await exchange_code_for_token(code)
    if not token_data:
        return HTMLResponse(content="<h1>Token exchange failed</h1>", status_code=400)

    access_token = token_data.get("access_token")
    user_info = await get_google_user_info(access_token)
    if not user_info:
        return HTMLResponse(content="<h1>Failed to fetch user info</h1>", status_code=400)

    email = user_info.get("email", "")
    name = user_info.get("name", email.split("@")[0] if "@" in email else "User")
    picture = user_info.get("picture", "")

    jwt_token = create_jwt_token(email=email, name=name, picture=picture)

    response = RedirectResponse(url="/", status_code=302)
    set_auth_cookie(response, jwt_token)
    logger.info("User authenticated: %s (%s)", name, email)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=302)
    clear_auth_cookie(response)
    return response


@router.get("/me")
async def me(user: dict = Depends(require_auth)):
    return {
        "authenticated": True,
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
    }


@router.get("/status")
async def auth_status(user: dict = Depends(optional_auth)):
    if user:
        return {
            "authenticated": True,
            "email": user.get("email"),
            "name": user.get("name"),
            "picture": user.get("picture"),
        }
    return {"authenticated": False}
