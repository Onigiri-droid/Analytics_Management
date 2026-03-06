# app/routers/dashboard.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.templates import templates
from app.services.dashboard_service import get_dashboard_data

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    data = get_dashboard_data(db)
    return templates.TemplateResponse("dashboard.html", {
        "request":     request,
        "active_page": "dashboard",
        **data,
    })
