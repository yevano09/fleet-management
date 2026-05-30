import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os

from app.auth import get_current_admin, get_active_sessions, revoke_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


def _require_admin(request: Request):
    admin = get_current_admin(request)
    if not admin:
        raise HTTPException(status_code=302, detail="Not authenticated")
    return admin


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    admin = get_current_admin(request)
    if not admin:
        return RedirectResponse(url="/auth/admin/login")
    sessions = await get_active_sessions()
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "admin": admin, "sessions": sessions},
    )


@router.get("/sessions")
async def list_sessions(request: Request):
    admin = get_current_admin(request)
    if not admin:
        return {"authenticated": False}
    sessions = await get_active_sessions()
    return {"sessions": sessions}


@router.post("/sessions/{session_id}/revoke")
async def disconnect_session(session_id: str, request: Request):
    admin = get_current_admin(request)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    success = await revoke_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}
