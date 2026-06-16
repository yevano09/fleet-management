import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os

from app.auth import get_current_user, get_current_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await get_current_user(request)
    admin = get_current_admin(request)
    if not user and not admin:
        return RedirectResponse(url="/auth/login")
    if admin:
        user = admin
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "user": user},
    )
