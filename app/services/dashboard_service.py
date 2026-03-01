from __future__ import annotations

from calendar import month_name
from typing import Any

from sqlalchemy import desc, func, literal, select
from sqlalchemy.orm import Session

from app.models.weekly_report import WeeklyReport, WeeklyReportItem


MONTHS_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


def _get_latest_and_previous_report(db: Session) -> tuple[WeeklyReport | None, WeeklyReport | None]:
    latest = db.execute(
        select(WeeklyReport).order_by(WeeklyReport.report_date.desc()).limit(1)
    ).scalar_one_or_none()
    if not latest:
        return None, None

    previous = db.execute(
        select(WeeklyReport)
        .where(WeeklyReport.report_date < latest.report_date)
        .order_by(WeeklyReport.report_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return latest, previous


def _get_sales_sum_rub(db: Session, report_id: int) -> float:
    value = db.execute(
        select(func.coalesce(func.sum(WeeklyReportItem.sales_qty * WeeklyReportItem.store_price), 0.0)).where(
            WeeklyReportItem.report_id == report_id
        )
    ).scalar_one()
    return float(value or 0.0)


def _get_weekly_sales_series(db: Session, limit: int = 8) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            WeeklyReport.report_date,
            func.coalesce(func.sum(WeeklyReportItem.sales_qty), 0.0).label("sales_qty"),
        )
        .join(WeeklyReportItem, WeeklyReport.id == WeeklyReportItem.report_id)
        .group_by(WeeklyReport.id, WeeklyReport.report_date)
        .order_by(WeeklyReport.report_date.desc())
        .limit(limit)
    ).all()

    series = [
    {"report_date": rdate.isoformat() if rdate else None, "sales_qty": float(sales or 0.0)} for rdate, sales in rows
    ]
    # для удобства отображения можно развернуть в хронологическом порядке
    return list(reversed(series))


def _get_category_distribution(db: Session, report_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            WeeklyReportItem.group3,
            func.coalesce(func.sum(WeeklyReportItem.sales_qty), 0.0).label("qty"),
        )
        .where(WeeklyReportItem.report_id == report_id)
        .group_by(WeeklyReportItem.group3)
    ).all()

    total = sum(float(qty or 0.0) for _, qty in rows)
    if total <= 0:
        return []

    result: list[dict[str, Any]] = []
    for group3, qty in rows:
        q = float(qty or 0.0)
        pct = (q / total * 100.0) if total > 0 else 0.0
        result.append(
            {
                "category_name": group3 or "Без категории",
                "total_qty": q,
                "percentage": round(float(pct), 2),
            }
        )
    # сортируем по объёму продаж
    result.sort(key=lambda x: x["total_qty"], reverse=True)
    return result


def _get_stock_kpi(db: Session, report_id: int) -> dict[str, Any]:
    total_sku = db.execute(
        select(func.count()).where(WeeklyReportItem.report_id == report_id)
    ).scalar_one()
    in_stock = db.execute(
        select(func.count())
        .where(
            WeeklyReportItem.report_id == report_id,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
        )
    ).scalar_one()
    critical = db.execute(
        select(func.count())
        .where(
            WeeklyReportItem.report_id == report_id,
            WeeklyReportItem.sales_qty.is_not(None),
            WeeklyReportItem.sales_qty > 0,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty <= WeeklyReportItem.sales_qty,
        )
    ).scalar_one()
    return {
        "in_stock": int(in_stock or 0),
        "total_sku": int(total_sku or 0),
        "critical_count": int(critical or 0),
    }


def _get_top_sellers(db: Session, latest: WeeklyReport, previous: WeeklyReport | None) -> list[dict[str, Any]]:
    # текущие продажи
    current_subq = (
        select(
            WeeklyReportItem.article.label("article"),
            WeeklyReportItem.name.label("name"),
            WeeklyReportItem.sales_qty.label("sales_qty"),
            WeeklyReportItem.store_price.label("store_price"),
        )
        .where(WeeklyReportItem.report_id == latest.id)
        .subquery()
    )

    if previous:
        prev_subq = (
            select(
                WeeklyReportItem.article.label("article"),
                WeeklyReportItem.sales_qty.label("prev_sales_qty"),
            )
            .where(WeeklyReportItem.report_id == previous.id)
            .subquery()
        )

        rows = db.execute(
            select(
                current_subq.c.article,
                current_subq.c.name,
                current_subq.c.sales_qty,
                current_subq.c.store_price,
                func.coalesce(prev_subq.c.prev_sales_qty, 0.0).label("prev_sales_qty"),
            )
            .outerjoin(prev_subq, prev_subq.c.article == current_subq.c.article)
            .order_by(current_subq.c.sales_qty.desc().nullslast())
            .limit(5)
        ).all()
    else:
        rows = db.execute(
            select(
                current_subq.c.article,
                current_subq.c.name,
                current_subq.c.sales_qty,
                current_subq.c.store_price,
                literal(0.0).label("prev_sales_qty"),
            )
            .order_by(current_subq.c.sales_qty.desc().nullslast())
            .limit(5)
        ).all()

    result: list[dict[str, Any]] = []
    for article, name, sales_qty, store_price, prev_sales_qty in rows:
        sales = float(sales_qty or 0.0)
        price = float(store_price or 0.0)
        prev_sales = float(prev_sales_qty or 0.0)
        revenue = sales * price
        delta = sales - prev_sales
        result.append(
            {
                "article": article,
                "name": name,
                "sales_qty": sales,
                "revenue": revenue,
                "delta_qty": delta,
            }
        )
    return result


def _get_top_restock(db: Session, latest: WeeklyReport) -> list[dict[str, Any]]:
    # кандидаты на дозакупку: те, у кого есть продажи и остаток
    rows = db.execute(
        select(
            WeeklyReportItem.article,
            WeeklyReportItem.name,
            WeeklyReportItem.sales_qty,
            WeeklyReportItem.stock_qty,
            (WeeklyReportItem.stock_qty / func.nullif(WeeklyReportItem.sales_qty, 0.0)).label("risk_ratio"),
        )
        .where(
            WeeklyReportItem.report_id == latest.id,
            WeeklyReportItem.sales_qty.is_not(None),
            WeeklyReportItem.sales_qty > 0,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
        )
        .order_by(
            WeeklyReportItem.sales_qty.desc(),
            WeeklyReportItem.stock_qty.asc(),
        )
        .limit(50)
    ).all()

    candidates: list[dict[str, Any]] = []
    for article, name, sales_qty, stock_qty, risk_ratio in rows:
        if risk_ratio is None:
            continue
        r = float(risk_ratio)
        if r < 1:
            status = "Критично"
        elif r < 2:
            status = "Низкий"
        else:
            continue  # нормальный уровень, не отображаем
        candidates.append(
            {
                "article": article,
                "name": name,
                "sales_qty": float(sales_qty or 0.0),
                "stock_qty": float(stock_qty or 0.0),
                "risk_ratio": r,
                "status": status,
            }
        )

    # уже отсортировано по продажам и остатку, берём top-5
    return candidates[:5]


def get_dashboard_data(db: Session) -> dict[str, Any]:
    latest, previous = _get_latest_and_previous_report(db)
    if not latest:
        return {
            "has_data": False,
            "title": "Обзор ключевых показателей",
        }

    month = MONTHS_RU.get(latest.report_date.month, month_name[latest.report_date.month])
    year = latest.report_date.year
    title = f"Обзор ключевых показателей за {month} {year}"

    current_sales = _get_sales_sum_rub(db, latest.id)
    previous_sales = _get_sales_sum_rub(db, previous.id) if previous else 0.0
    if previous_sales > 0:
        growth_pct = (current_sales - previous_sales) / previous_sales * 100.0
    else:
        growth_pct = None

    stock_kpi = _get_stock_kpi(db, latest.id)
    weekly_series = _get_weekly_sales_series(db, limit=8)
    category_distribution = _get_category_distribution(db, latest.id)
    top_sellers = _get_top_sellers(db, latest, previous)
    top_restock = _get_top_restock(db, latest)

    return {
        "has_data": True,
        "title": title,
        "kpi_sales": {
            "current": current_sales,
            "previous": previous_sales,
            "growth_pct": growth_pct,
        },
        "kpi_stock": {
            "in_stock": stock_kpi["in_stock"],
            "total_sku": stock_kpi["total_sku"],
            "critical_count": stock_kpi["critical_count"],
        },
        "kpi_turnover": {
            "value": "2.3",
            "status": "Отличный уровень",
        },
        "weekly_sales": weekly_series,
        "categories": category_distribution,
        "top_sellers": top_sellers,
        "top_restock": top_restock,
    }

