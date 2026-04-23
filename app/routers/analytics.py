# app/routers/analytics.py
from datetime import date
import math

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.templates import templates
from app.services.analytics_service import (
    build_analytics_table,
    get_product_detail,
    get_seasonality,
    get_trend,
    get_turnover,
    get_weeks_without_sales,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def analytics_page(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    page: int = Query(1, ge=1),
    offset: int | None = Query(None, ge=0),
    q: str | None = Query(None),
    g1: str | None = Query(None),
    g2: str | None = Query(None),
    g3: str | None = Query(None),
    kpi: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q_norm = (q or "").strip()
    g1_norm = (g1 or "").strip()
    g2_norm = (g2 or "").strip()
    g3_norm = (g3 or "").strip()
    kpi_norm = ((kpi or "all").strip() or "all")
    if offset is not None:
        safe_offset = offset
        page = (offset // limit) + 1
    else:
        safe_offset = (page - 1) * limit

    data = build_analytics_table(
        db,
        limit=limit,
        offset=safe_offset,
        q=q_norm,
        group1=g1_norm,
        group2=g2_norm,
        group3=g3_norm,
        kpi_filter=kpi_norm,
    )

    kpi_data = data.get("kpi", {}) or {}
    if kpi_norm == "in_stock":
        total_items = int(kpi_data.get("in_stock", 0))
    elif kpi_norm == "low_critical":
        total_items = int(kpi_data.get("low_critical_count", 0))
    elif kpi_norm == "seasonal":
        total_items = int(kpi_data.get("seasonal_count", 0))
    else:
        total_items = int(kpi_data.get("total_sku", 0))

    total_pages = max(1, math.ceil(total_items / limit)) if total_items > 0 else 1
    if page > total_pages:
        page = total_pages
        safe_offset = (page - 1) * limit
        data = build_analytics_table(
            db,
            limit=limit,
            offset=safe_offset,
            q=q_norm,
            group1=g1_norm,
            group2=g2_norm,
            group3=g3_norm,
            kpi_filter=kpi_norm,
        )

    page_start = max(1, page - 2)
    page_end = min(total_pages, page + 2)
    return templates.TemplateResponse(
        "analytics.html",
        {
            "request":          request,
            "active_page":      "analytics",
            # данные отчёта
            "report_date":      data["report_date"],
            "prev_report_date": data["prev_report_date"],
            # kpi-карточки
            "kpi":              data["kpi"],
            # фильтры
            "groups1":          data["groups1"],
            "groups2":          data["groups2"],
            "groups3":          data["groups3"],
            # таблица
            "rows":             data["rows"],
            "filtered_mode":    bool(q_norm or g1_norm or g2_norm or g3_norm or (kpi_norm != "all")),
            "filter_q":         q_norm,
            "filter_g1":        g1_norm,
            "filter_g2":        g2_norm,
            "filter_g3":        g3_norm,
            "filter_kpi":       kpi_norm,
            "page":             page,
            "total_pages":      total_pages,
            "page_start":       page_start,
            "page_end":         page_end,
            "limit":            limit,
            "offset":           safe_offset,
        },
    )


# Детальный API по артикулу (не трогаем)
@router.get("/{article}")
def analytics_article(
    article: str,
    weeks: int = Query(4, ge=2, le=52),
    report_date: date | None = None,
    db: Session = Depends(get_db),
):
    return {
        "weeks_without_sales": get_weeks_without_sales(db, article=article),
        "seasonality":         get_seasonality(db, article=article),
        "trend":               get_trend(db, article=article, weeks=weeks),
        "turnover":            get_turnover(db, article=article, report_date=report_date),
    }


@router.get("/item/{article}", response_class=HTMLResponse)
def product_item_page(
    request: Request,
    article: str,
    weeks: int = Query(4, ge=2, le=52),
    return_to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    detail = get_product_detail(db, article=article, weeks=weeks)
    back_url = return_to if return_to and return_to.startswith("/analytics") else "/analytics/"
    return templates.TemplateResponse(
        "product_analytics.html",
        {
            "request":     request,
            "active_page": "analytics",
            "detail":      detail,
            "back_url":    back_url,
        },
    )
