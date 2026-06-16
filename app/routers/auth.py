import secrets
import logging

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse

from app.auth import (
    get_google_auth_url,
    exchange_code_for_token,
    get_google_user_info,
    create_user_jwt_token,
    create_admin_jwt_token,
    set_auth_cookie,
    clear_auth_cookie,
    set_admin_cookie,
    clear_admin_cookie,
    create_session,
    revoke_session,
    get_current_user,
    get_current_admin,
    COOKIE_NAME,
)
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Google OAuth routes (user dashboard) ────────────────────────────────

@router.get("/login")
async def login():
    return HTMLResponse(content=LOGIN_LANDING_PAGE, status_code=200)


@router.get("/google/login")
async def google_login():
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

    session_id = await create_session(email=email, name=name, picture=picture)
    jwt_token = create_user_jwt_token(email=email, name=name, picture=picture, session_id=session_id)

    response = RedirectResponse(url="/", status_code=302)
    set_auth_cookie(response, jwt_token)
    logger.info("User authenticated: %s (%s)", name, email)
    return response


@router.get("/logout")
async def logout(request: Request):
    user = await get_current_user(request)
    if user and user.get("session_id"):
        await revoke_session(user["session_id"])
    response = RedirectResponse(url="/auth/login", status_code=302)
    clear_auth_cookie(response)
    return response



@router.get("/me")
async def me(request: Request):
    user = await get_current_user(request)
    if not user:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
    }


@router.get("/status")
async def auth_status(request: Request):
    user = await get_current_user(request)
    if user:
        return {
            "authenticated": True,
            "email": user.get("email"),
            "name": user.get("name"),
            "picture": user.get("picture"),
        }
    return {"authenticated": False}


# ── Admin auth routes ────────────────────────────────────────────────────

@router.get("/admin/login")
async def admin_login_page():
    return HTMLResponse(content=ADMIN_LOGIN_PAGE, status_code=200)


@router.post("/admin/login")
async def admin_login(
    username: str = Form(...),
    password: str = Form(...),
):
    if not secrets.compare_digest(username, settings.admin_username) or not secrets.compare_digest(password, settings.admin_password):
        return HTMLResponse(content=ADMIN_LOGIN_ERROR, status_code=200)

    token = create_admin_jwt_token(username=username)
    response = RedirectResponse(url="/", status_code=302)
    set_admin_cookie(response, token)
    logger.info("Admin logged in: %s", username)
    return response


@router.get("/admin/logout")
async def admin_logout():
    response = RedirectResponse(url="/auth/login", status_code=302)
    clear_admin_cookie(response)
    return response


# ── Inline HTML pages ────────────────────────────────────────────────────

LOGIN_LANDING_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fleet Commander - Login</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: system-ui, -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; display:flex; justify-content:center; align-items:center; }
.card { background:#1e293b; border:1px solid #334155; border-radius:0.75rem; padding:2.5rem; width:100%; max-width:420px; text-align:center; }
.card h1 { font-size:1.5rem; color:#38bdf8; margin-bottom:0.25rem; }
.card .subtitle { color:#94a3b8; font-size:0.875rem; margin-bottom:2rem; }
.card .divider { display:flex; align-items:center; gap:0.75rem; margin:1.25rem 0; color:#475569; font-size:0.75rem; text-transform:uppercase; }
.card .divider::before, .card .divider::after { content:""; flex:1; border-top:1px solid #334155; }
.btn { display:block; width:100%; padding:0.75rem; border:none; border-radius:0.375rem; font-size:0.875rem; font-weight:600; cursor:pointer; text-decoration:none; text-align:center; }
.btn-google { background:white; color:#1e293b; }
.btn-google:hover { background:#f1f5f9; }
.btn-admin { background:transparent; color:#94a3b8; border:1px solid #334155; }
.btn-admin:hover { border-color:#38bdf8; color:#38bdf8; }
</style>
</head>
<body>
<div class="card">
<h1>Fleet Commander</h1>
<div class="subtitle">IoT Fleet Management Dashboard</div>
<a href="/auth/google/login" class="btn btn-google">Sign in with Google</a>
<div class="divider">or</div>
<a href="/auth/admin/login" class="btn btn-admin">Admin Sign In</a>
</div>
</body>
</html>"""

ADMIN_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin Login - Fleet Commander</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: system-ui, -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; display:flex; justify-content:center; align-items:center; }
.login-card { background:#1e293b; border:1px solid #334155; border-radius:0.75rem; padding:2.5rem; width:100%; max-width:400px; }
.login-card h1 { font-size:1.5rem; color:#38bdf8; margin-bottom:0.25rem; }
.login-card .subtitle { color:#94a3b8; font-size:0.875rem; margin-bottom:1.5rem; }
.login-card form { display:flex; flex-direction:column; gap:1rem; }
.login-card label { font-size:0.875rem; color:#94a3b8; }
.login-card input { padding:0.75rem; background:#0f172a; border:1px solid #334155; border-radius:0.375rem; color:#e2e8f0; font-size:0.875rem; }
.login-card input:focus { outline:none; border-color:#38bdf8; }
.btn { padding:0.75rem; border:none; border-radius:0.375rem; font-size:0.875rem; font-weight:600; cursor:pointer; }
.btn-primary { background:#2563eb; color:white; }
.btn-primary:hover { background:#1d4ed8; }
.error { display:none; background:rgba(248,113,113,0.15); color:#f87171; padding:0.75rem; border-radius:0.375rem; font-size:0.875rem; }
</style>
</head>
<body>
<div class="login-card">
<h1>Fleet Commander</h1>
<div class="subtitle">Admin Panel Login</div>
<div class="error" id="error"></div>
<form method="POST" action="/auth/admin/login">
<label for="username">Username</label>
<input type="text" id="username" name="username" required autocomplete="username">
<label for="password">Password</label>
<input type="password" id="password" name="password" required autocomplete="current-password">
<button type="submit" class="btn btn-primary">Sign In</button>
</form>
</div>
</body>
</html>"""

ADMIN_LOGIN_ERROR = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin Login - Fleet Commander</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: system-ui, -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; display:flex; justify-content:center; align-items:center; }
.login-card { background:#1e293b; border:1px solid #334155; border-radius:0.75rem; padding:2.5rem; width:100%; max-width:400px; }
.login-card h1 { font-size:1.5rem; color:#38bdf8; margin-bottom:0.25rem; }
.login-card .subtitle { color:#94a3b8; font-size:0.875rem; margin-bottom:1.5rem; }
.login-card form { display:flex; flex-direction:column; gap:1rem; }
.login-card label { font-size:0.875rem; color:#94a3b8; }
.login-card input { padding:0.75rem; background:#0f172a; border:1px solid #334155; border-radius:0.375rem; color:#e2e8f0; font-size:0.875rem; }
.login-card input:focus { outline:none; border-color:#38bdf8; }
.btn { padding:0.75rem; border:none; border-radius:0.375rem; font-size:0.875rem; font-weight:600; cursor:pointer; }
.btn-primary { background:#2563eb; color:white; }
.btn-primary:hover { background:#1d4ed8; }
.error { display:block; background:rgba(248,113,113,0.15); color:#f87171; padding:0.75rem; border-radius:0.375rem; font-size:0.875rem; }
</style>
</head>
<body>
<div class="login-card">
<h1>Fleet Commander</h1>
<div class="subtitle">Admin Panel Login</div>
<div class="error">Invalid username or password</div>
<form method="POST" action="/auth/admin/login">
<label for="username">Username</label>
<input type="text" id="username" name="username" required autocomplete="username">
<label for="password">Password</label>
<input type="password" id="password" name="password" required autocomplete="current-password">
<button type="submit" class="btn btn-primary">Sign In</button>
</form>
</div>
</body>
</html>"""
