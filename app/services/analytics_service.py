# app/services/analytics_service.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from sqlalchemy import desc, func, select
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
    return db.execute(
        select(WeeklyReport).order_by(desc(WeeklyReport.report_date)).limit(1)
    ).scalar_one_or_none()


def _get_previous_report(db: Session, latest: WeeklyReport) -> WeeklyReport | None:
    return db.execute(
        select(WeeklyReport)
        .where(WeeklyReport.report_date < latest.report_date)
        .order_by(desc(WeeklyReport.report_date))
        .limit(1)
    ).scalar_one_or_none()


def _load_history_for_articles(
    db: Session, *, articles: Iterable[str]
) -> dict[str, list[ArticlePoint]]:
    articles_list = [a for a in set(articles) if a]
    if not articles_list:
        return {}

    rows = db.execute(
        select(
            WeeklyReportItem.article,
            WeeklyReport.report_date,
            WeeklyReportItem.sales_qty,
            WeeklyReportItem.stock_qty,
        )
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


# ─────────────────────────────────────────────────────────────────────────────
# Существующие API-методы (не трогаем)
# ─────────────────────────────────────────────────────────────────────────────

def get_weeks_without_sales(db: Session, *, article: str) -> dict[str, Any]:
    history = _load_history_for_articles(db, articles=[article]).get(article, [])
    if not history:
        return {"article": article, "weeks_without_sales": 0, "stock_qty": 0, "last_sale_date": None}

    weeks = 0
    last_sale_date: date | None = None
    for p in history:
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
    pct = (
        ((last_avg - prev_avg) / prev_avg * 100.0)
        if prev_avg > 0
        else (100.0 if last_avg > 0 else 0.0)
    )
    trend = "up" if pct > 5 else "down" if pct < -5 else "flat"

    return {
        "article": article,
        "trend": trend,
        "pct_change": round(float(pct), 2),
        "series": sales,
    }


def get_turnover(
    db: Session, *, article: str, report_date: date | None = None
) -> dict[str, Any]:
    q = (
        select(
            WeeklyReportItem.stock_qty,
            WeeklyReportItem.sales_qty,
            WeeklyReport.report_date,
        )
        .join(WeeklyReport, WeeklyReport.id == WeeklyReportItem.report_id)
        .where(WeeklyReportItem.article == article)
    )
    if report_date:
        q = q.where(WeeklyReport.report_date == report_date)
    q = q.order_by(desc(WeeklyReport.report_date)).limit(1)

    row = db.execute(q).first()
    if not row:
        return {
            "article": article,
            "report_date": None,
            "turnover": 0.0,
            "sales_qty": 0.0,
            "stock_qty": 0.0,
        }

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


# ─────────────────────────────────────────────────────────────────────────────
# Основная таблица остатков — расширенная версия
# ─────────────────────────────────────────────────────────────────────────────

def build_analytics_table(
    db: Session, *, limit: int = 200, offset: int = 0
) -> dict[str, Any]:
    """
    Возвращает:
      - report_date, prev_report_date
      - kpi: total_sku, in_stock, critical_count, seasonal_count
      - groups1/2/3 — уникальные значения для фильтров
      - rows — список товаров с расширенными полями
    """
    latest = get_latest_report(db)
    if not latest:
        return {
            "report_date": None,
            "prev_report_date": None,
            "kpi": {"total_sku": 0, "in_stock": 0, "critical_count": 0, "seasonal_count": 0},
            "groups1": [],
            "groups2": [],
            "groups3": [],
            "rows": [],
        }

    previous = _get_previous_report(db, latest)

    # ── Все позиции актуального отчёта ──────────────────────────────────
    items = db.execute(
        select(
            WeeklyReportItem.article,
            WeeklyReportItem.name,
            WeeklyReportItem.group1,
            WeeklyReportItem.group2,
            WeeklyReportItem.group3,
            WeeklyReportItem.stock_qty,
            WeeklyReportItem.sales_qty,
            WeeklyReportItem.store_price,
            WeeklyReportItem.actual_margin_pct,
        )
        .where(WeeklyReportItem.report_id == latest.id)
        .order_by(WeeklyReportItem.article.asc())
        .limit(limit)
        .offset(offset)
    ).all()

    # ── Продажи из предыдущего отчёта для сравнения ─────────────────────
    prev_sales_map: dict[str, float] = {}
    if previous:
        prev_rows = db.execute(
            select(WeeklyReportItem.article, WeeklyReportItem.sales_qty)
            .where(WeeklyReportItem.report_id == previous.id)
        ).all()
        for art, sq in prev_rows:
            if art:
                prev_sales_map[str(art)] = _to_float(sq)

    # ── История для тренда/сезонности ───────────────────────────────────
    articles = [str(a) for a, *_ in items if a]
    history_map = _load_history_for_articles(db, articles=articles)

    # ── Уникальные группы для фильтров (из ВСЕГО отчёта, не только limit) ─
    all_groups = db.execute(
        select(
            WeeklyReportItem.group1,
            WeeklyReportItem.group2,
            WeeklyReportItem.group3,
        )
        .where(WeeklyReportItem.report_id == latest.id)
        .distinct()
    ).all()

    groups1: list[str] = sorted({g1 for g1, _, _ in all_groups if g1})
    groups2: list[str] = sorted({g2 for _, g2, _ in all_groups if g2})
    groups3: list[str] = sorted({g3 for _, _, g3 in all_groups if g3})

    # ── KPI ─────────────────────────────────────────────────────────────
    total_sku = db.execute(
        select(func.count()).where(WeeklyReportItem.report_id == latest.id)
    ).scalar_one()
    in_stock = db.execute(
        select(func.count()).where(
            WeeklyReportItem.report_id == latest.id,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
        )
    ).scalar_one()
    critical = db.execute(
        select(func.count()).where(
            WeeklyReportItem.report_id == latest.id,
            WeeklyReportItem.sales_qty.is_not(None),
            WeeklyReportItem.sales_qty > 0,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
            WeeklyReportItem.stock_qty <= WeeklyReportItem.sales_qty,
        )
    ).scalar_one()

    # ── Строки таблицы ───────────────────────────────────────────────────
    rows: list[dict[str, Any]] = []
    seasonal_count = 0

    for article, name, group1, group2, group3, stock_qty, sales_qty, store_price, margin_pct in items:
        art = str(article) if article is not None else ""
        hist = history_map.get(art, [])

        # Недели без продаж
        weeks_wo = 0
        last_sale_date: date | None = None
        for p in hist:
            if p.sales_qty > 0:
                last_sale_date = p.report_date
                break
            weeks_wo += 1

        # Тренд
        sales_series = [p.sales_qty for p in hist[:4]]
        if len(sales_series) >= 2:
            mid = len(sales_series) // 2
            prev_s = sales_series[mid:]
            last_s = sales_series[:mid]
            prev_avg = sum(prev_s) / len(prev_s) if prev_s else 0.0
            last_avg = sum(last_s) / len(last_s) if last_s else 0.0
            pct = (
                ((last_avg - prev_avg) / prev_avg * 100.0)
                if prev_avg > 0
                else (100.0 if last_avg > 0 else 0.0)
            )
        else:
            pct = 0.0
        trend = "up" if pct > 5 else "down" if pct < -5 else "flat"

        # Сезонность
        month_sales: dict[int, float] = defaultdict(float)
        for p in hist:
            month_sales[p.report_date.month] += p.sales_qty
        peak_month = (
            max(month_sales.items(), key=lambda x: x[1])[0] if month_sales else None
        )
        is_seasonal = peak_month is not None and len(month_sales) >= 2
        if is_seasonal:
            seasonal_count += 1

        # Критический порог (продажи × 4 недели)
        s = _to_float(sales_qty)
        st = _to_float(stock_qty)
        critical_threshold = s * 4  # запас на 4 недели
        prev_s_val = prev_sales_map.get(art, None)

        # Продажи в день (≈ sales_qty / 7)
        sales_per_day = round(s / 7, 1) if s > 0 else 0.0

        # Статус остатка
        if s > 0 and st > 0:
            ratio = st / s
            if ratio < 1:
                stock_status = "critical"
            elif ratio < 2:
                stock_status = "low"
            else:
                stock_status = "ok"
        elif st == 0:
            stock_status = "empty"
        else:
            stock_status = "ok"

        rows.append(
            {
                "article": art,
                "name": name or "",
                "group1": group1 or "",
                "group2": group2 or "",
                "group3": group3 or "",
                "stock_qty": st,
                "sales_qty": s,
                "sales_per_day": sales_per_day,
                "prev_sales_qty": prev_s_val,
                "store_price": _to_float(store_price),
                "actual_margin_pct": _to_float(margin_pct),
                "critical_threshold": round(critical_threshold),
                "stock_status": stock_status,       # ok / low / critical / empty
                "weeks_without_sales": weeks_wo,
                "last_sale_date": str(last_sale_date) if last_sale_date else None,
                "peak_month": peak_month,
                "is_seasonal": is_seasonal,
                "trend": trend,
                "trend_pct": round(float(pct), 2),
            }
        )

    return {
        "report_date": str(latest.report_date),
        "prev_report_date": str(previous.report_date) if previous else None,
        "kpi": {
            "total_sku": int(total_sku),
            "in_stock": int(in_stock),
            "critical_count": int(critical),
            "seasonal_count": seasonal_count,
        },
        "groups1": groups1,
        "groups2": groups2,
        "groups3": groups3,
        "rows": rows,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Средний оборот для дашборда
# ─────────────────────────────────────────────────────────────────────────────

def get_avg_turnover(db: Session) -> dict[str, Any]:
    """
    Считает средний оборот по последнему отчёту:
      оборот позиции = sales_qty / stock_qty (в неделях)
    Усредняем по позициям с ненулевым остатком.
    Переводим в раз/месяц: умножаем на ~4.33 (недель в месяце).
    """
    latest = get_latest_report(db)
    if not latest:
        return {"value": None, "status": "Нет данных"}

    rows = db.execute(
        select(WeeklyReportItem.sales_qty, WeeklyReportItem.stock_qty)
        .where(
            WeeklyReportItem.report_id == latest.id,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
            WeeklyReportItem.sales_qty.is_not(None),
            WeeklyReportItem.sales_qty > 0,
        )
    ).all()

    if not rows:
        return {"value": "0.0", "status": "Нет продаж"}

    turnovers = [
        _to_float(sales) / _to_float(stock)
        for sales, stock in rows
        if _to_float(stock) > 0
    ]
    if not turnovers:
        return {"value": "0.0", "status": "Нет данных"}

    avg_weekly = sum(turnovers) / len(turnovers)
    avg_monthly = avg_weekly * 4.33  # переводим недели → месяц

    if avg_monthly >= 4:
        status = "Отличный уровень"
    elif avg_monthly >= 2:
        status = "Хороший уровень"
    elif avg_monthly >= 1:
        status = "Удовлетворительно"
    else:
        status = "Низкий оборот"

    return {
        "value": str(round(avg_monthly, 1)),
        "status": status,
    }


def get_product_detail(
    db: Session, *, article: str, weeks: int = 4
) -> dict[str, Any]:
    """
    Детальная аналитика по одному артикулу для отдельной страницы.
    Возвращает агрегаты и данные для графиков.
    """
    latest = get_latest_report(db)
    if not latest:
        return {"found": False, "article": article}

    previous = _get_previous_report(db, latest)

    # Текущие атрибуты товара (имя/группы/цена/маржа) берём из последнего отчёта.
    latest_item = db.execute(
        select(
            WeeklyReportItem.article,
            WeeklyReportItem.name,
            WeeklyReportItem.group1,
            WeeklyReportItem.group2,
            WeeklyReportItem.group3,
            WeeklyReportItem.stock_qty,
            WeeklyReportItem.sales_qty,
            WeeklyReportItem.store_price,
            WeeklyReportItem.actual_margin_pct,
        )
        .where(
            WeeklyReportItem.report_id == latest.id,
            WeeklyReportItem.article == article,
        )
        .limit(1)
    ).first()

    name = ""
    group1 = ""
    group2 = ""
    group3 = ""
    stock_qty = 0.0
    sales_qty = 0.0
    store_price = None
    actual_margin_pct = None

    if latest_item:
        _, name, group1, group2, group3, st, sq, sp, mp = latest_item
        stock_qty = _to_float(st)
        sales_qty = _to_float(sq)
        store_price = _to_float(sp) if sp is not None else None
        actual_margin_pct = _to_float(mp) if mp is not None else None

    weeks_wo = get_weeks_without_sales(db, article=article)
    seasonality = get_seasonality(db, article=article)
    trend = get_trend(db, article=article, weeks=weeks)
    turnover_latest = get_turnover(db, article=article, report_date=latest.report_date)

    turnover_prev: dict[str, Any] | None = None
    prev_report_date: date | None = None
    if previous:
        prev_report_date = previous.report_date
        turnover_prev = get_turnover(db, article=article, report_date=previous.report_date)

    prev_stock_qty = (
        _to_float(turnover_prev.get("stock_qty")) if turnover_prev else 0.0
    )
    prev_sales_qty = (
        _to_float(turnover_prev.get("sales_qty")) if turnover_prev else 0.0
    )

    sales_change_pct = 0.0
    if prev_sales_qty > 0:
        sales_change_pct = ((sales_qty - prev_sales_qty) / prev_sales_qty) * 100.0
    else:
        sales_change_pct = 100.0 if sales_qty > 0 else 0.0

    stock_change_qty = stock_qty - prev_stock_qty

    turnover_curr = _to_float(turnover_latest.get("turnover"))
    turnover_prev_val = _to_float(turnover_prev.get("turnover")) if turnover_prev else 0.0
    turnover_change_pct = 0.0
    if turnover_prev_val > 0:
        turnover_change_pct = ((turnover_curr - turnover_prev_val) / turnover_prev_val) * 100.0
    else:
        turnover_change_pct = 100.0 if turnover_curr > 0 else 0.0

    # Статус остатка — та же логика, что и на главной странице остатков.
    if sales_qty > 0 and stock_qty > 0:
        ratio = stock_qty / sales_qty
        if ratio < 1:
            stock_status = "critical"
        elif ratio < 2:
            stock_status = "low"
        else:
            stock_status = "ok"
    elif stock_qty == 0:
        stock_status = "empty"
    else:
        stock_status = "ok"

    return {
        "found": latest_item is not None,
        "article": article,
        "name": name,
        "group1": group1,
        "group2": group2,
        "group3": group3,
        "report_date": str(latest.report_date),
        "prev_report_date": str(prev_report_date) if prev_report_date else None,
        # Текущие показатели
        "stock_qty": stock_qty,
        "sales_qty": sales_qty,
        "sales_per_day": round(sales_qty / 7, 1) if sales_qty > 0 else 0.0,
        "store_price": store_price,
        "actual_margin_pct": actual_margin_pct,
        "stock_status": stock_status,
        # Сравнение с предыдущим отчётом
        "prev_sales_qty": prev_sales_qty,
        "prev_stock_qty": prev_stock_qty,
        "sales_change_pct": round(float(sales_change_pct), 2),
        "stock_change_qty": round(float(stock_change_qty), 0),
        # Отдельные метрики для блоков/графиков
        "weeks_without_sales": int(weeks_wo.get("weeks_without_sales") or 0),
        "last_sale_date": weeks_wo.get("last_sale_date"),
        "seasonality": seasonality,
        "trend": trend,
        "turnover_latest": turnover_latest,
        "turnover_prev": turnover_prev,
        "turnover_change_pct": round(float(turnover_change_pct), 2),
    }
