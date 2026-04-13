from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from app.core.auth import LoginRequired, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.templates import templates
from app.routers import api_router
from app.services.dashboard_service import get_dashboard_data

app = FastAPI(title="Inventory and Procurement Management")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=not settings.debug,
    max_age=settings.session_max_age_seconds,
)

app.include_router(api_router)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse(url="/auth/", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    data = get_dashboard_data(db)
    context = {"request": request, "active_page": "dashboard", **data}
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/login")
async def redirect_login():
    return RedirectResponse(url="/auth/", status_code=303)


@app.get("/health")
async def health():
    return {"status": "ok"}
