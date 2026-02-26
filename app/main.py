from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.templates import templates
from app.routers import api_router
from app.services.dashboard_service import get_dashboard_data

app = FastAPI(title="Inventory and Procurement Management")

app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    data = get_dashboard_data(db)
    context = {"request": request, **data}
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/health")
async def health():
    return {"status": "ok"}
