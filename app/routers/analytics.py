from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.templates import templates
from app.services.analytics_service import (
    build_analytics_table,
    get_seasonality,
    get_trend,
    get_turnover,
    get_weeks_without_sales,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def analytics_page(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    data = build_analytics_table(db, limit=limit, offset=offset)
    return templates.TemplateResponse(
        "analytics.html",
        {"request": request, "report_date": data["report_date"], "rows": data["rows"], "limit": limit, "offset": offset},
    )


@router.get("/{article}")
def analytics_article(
    article: str,
    weeks: int = Query(4, ge=2, le=52),
    report_date: date | None = None,
    db: Session = Depends(get_db),
):
    return {
        "weeks_without_sales": get_weeks_without_sales(db, article=article),
        "seasonality": get_seasonality(db, article=article),
        "trend": get_trend(db, article=article, weeks=weeks),
        "turnover": get_turnover(db, article=article, report_date=report_date),
    }

