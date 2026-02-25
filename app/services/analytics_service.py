from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.weekly_report import WeeklyReport, WeeklyReportItem


@dataclass(frozen=True)
class ArticlePoint:
    report_date: date
    sales_qty: float
    stock_qty: float


def _to_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def get_latest_report(db: Session) -> WeeklyReport | None:
    return db.execute(select(WeeklyReport).order_by(desc(WeeklyReport.report_date)).limit(1)).scalar_one_or_none()


def _load_history_for_articles(db: Session, *, articles: Iterable[str]) -> dict[str, list[ArticlePoint]]:
    articles_list = [a for a in set(articles) if a]
    if not articles_list:
        return {}

    rows = db.execute(
        select(WeeklyReportItem.article, WeeklyReport.report_date, WeeklyReportItem.sales_qty, WeeklyReportItem.stock_qty)
        .join(WeeklyReport, WeeklyReport.id == WeeklyReportItem.report_id)
        .where(WeeklyReportItem.article.in_(articles_list))
        .order_by(WeeklyReportItem.article.asc(), WeeklyReport.report_date.desc())
    ).all()

    out: dict[str, list[ArticlePoint]] = defaultdict(list)
    for article, report_date, sales_qty, stock_qty in rows:
        out[str(article)].append(
            ArticlePoint(
                report_date=report_date,
                sales_qty=_to_float(sales_qty),
                stock_qty=_to_float(stock_qty),
            )
        )
    return out


def get_weeks_without_sales(db: Session, *, article: str) -> dict[str, Any]:
    history = _load_history_for_articles(db, articles=[article]).get(article, [])
    if not history:
        return {"article": article, "weeks_without_sales": 0, "stock_qty": 0, "last_sale_date": None}

    weeks = 0
    last_sale_date: date | None = None
    for p in history:  # история уже отсортирована от последнего отчёта к старым
        if p.sales_qty > 0:
            last_sale_date = p.report_date
            break
        weeks += 1

    latest_stock = history[0].stock_qty
    return {
        "article": article,
        "weeks_without_sales": weeks,
        "stock_qty": latest_stock,
        "last_sale_date": str(last_sale_date) if last_sale_date else None,
    }


def get_seasonality(db: Session, *, article: str) -> dict[str, Any]:
    history = _load_history_for_articles(db, articles=[article]).get(article, [])
    month_sales: dict[int, float] = defaultdict(float)
    for p in history:
        month_sales[p.report_date.month] += p.sales_qty

    if not month_sales:
        return {"article": article, "monthly_sales": {}, "peak_month": None, "seasonality_coef": 0.0}

    peak_month = max(month_sales.items(), key=lambda x: x[1])[0] if month_sales else None
    non_zero = [v for v in month_sales.values() if v > 0]
    avg = (sum(non_zero) / len(non_zero)) if non_zero else 0.0
    peak_val = month_sales.get(peak_month, 0.0) if peak_month else 0.0
    coef = (peak_val / avg) if avg > 0 else 0.0

    return {
        "article": article,
        "monthly_sales": {str(k): float(v) for k, v in sorted(month_sales.items())},
        "peak_month": peak_month,
        "seasonality_coef": round(float(coef), 4),
    }


def get_trend(db: Session, *, article: str, weeks: int = 4) -> dict[str, Any]:
    history = _load_history_for_articles(db, articles=[article]).get(article, [])
    points = history[: max(2, int(weeks))]
    sales = [p.sales_qty for p in points]

    if len(sales) < 2:
        return {"article": article, "trend": "flat", "pct_change": 0.0, "series": sales}

    mid = len(sales) // 2
    prev = sales[mid:]
    last = sales[:mid]

    prev_avg = sum(prev) / len(prev) if prev else 0.0
    last_avg = sum(last) / len(last) if last else 0.0
    pct = ((last_avg - prev_avg) / prev_avg * 100.0) if prev_avg > 0 else (100.0 if last_avg > 0 else 0.0)

    if pct > 5:
        trend = "up"
    elif pct < -5:
        trend = "down"
    else:
        trend = "flat"

    return {
        "article": article,
        "trend": trend,
        "pct_change": round(float(pct), 2),
        "series": sales,
    }


def get_turnover(db: Session, *, article: str, report_date: date | None = None) -> dict[str, Any]:
    q = (
        select(WeeklyReportItem.stock_qty, WeeklyReportItem.sales_qty, WeeklyReport.report_date)
        .join(WeeklyReport, WeeklyReport.id == WeeklyReportItem.report_id)
        .where(WeeklyReportItem.article == article)
    )
    if report_date:
        q = q.where(WeeklyReport.report_date == report_date)
    q = q.order_by(desc(WeeklyReport.report_date)).limit(1)

    row = db.execute(q).first()
    if not row:
        return {"article": article, "report_date": None, "turnover": 0.0, "sales_qty": 0.0, "stock_qty": 0.0}

    stock_qty, sales_qty, rdate = row
    stock = _to_float(stock_qty)
    sales = _to_float(sales_qty)
    turnover = (sales / stock) if stock > 0 else 0.0

    return {
        "article": article,
        "report_date": str(rdate),
        "turnover": round(float(turnover), 6),
        "sales_qty": sales,
        "stock_qty": stock,
    }


def build_analytics_table(db: Session, *, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    latest = get_latest_report(db)
    if not latest:
        return {"report_date": None, "rows": []}

    items = db.execute(
        select(WeeklyReportItem.article, WeeklyReportItem.name, WeeklyReportItem.stock_qty)
        .where(WeeklyReportItem.report_id == latest.id)
        .order_by(WeeklyReportItem.article.asc())
        .limit(limit)
        .offset(offset)
    ).all()

    articles = [str(a) for a, _, _ in items if a]
    history_map = _load_history_for_articles(db, articles=articles)

    rows: list[dict[str, Any]] = []
    for article, name, stock_qty in items:
        art = str(article) if article is not None else ""
        hist = history_map.get(art, [])

        # weeks_without_sales + last_sale_date
        weeks_wo = 0
        last_sale_date: date | None = None
        for p in hist:
            if p.sales_qty > 0:
                last_sale_date = p.report_date
                break
            weeks_wo += 1

        # trend (по 4 последним)
        sales_series = [p.sales_qty for p in hist[:4]]
        if len(sales_series) >= 2:
            mid = len(sales_series) // 2
            prev = sales_series[mid:]
            last = sales_series[:mid]
            prev_avg = sum(prev) / len(prev) if prev else 0.0
            last_avg = sum(last) / len(last) if last else 0.0
            pct = ((last_avg - prev_avg) / prev_avg * 100.0) if prev_avg > 0 else (100.0 if last_avg > 0 else 0.0)
        else:
            pct = 0.0
        trend = "up" if pct > 5 else "down" if pct < -5 else "flat"

        # сезонность: пик месяца по суммарным продажам
        month_sales: dict[int, float] = defaultdict(float)
        for p in hist:
            month_sales[p.report_date.month] += p.sales_qty
        peak_month = max(month_sales.items(), key=lambda x: x[1])[0] if month_sales else None

        rows.append(
            {
                "article": art,
                "name": name or "",
                "weeks_without_sales": weeks_wo,
                "last_sale_date": str(last_sale_date) if last_sale_date else None,
                "peak_month": peak_month,
                "trend": trend,
                "trend_pct": round(float(pct), 2),
                "stock_qty": _to_float(stock_qty),
            }
        )

    return {"report_date": str(latest.report_date), "rows": rows}

