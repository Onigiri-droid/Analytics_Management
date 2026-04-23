# app/services/analytics_service.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import math
from typing import Any, Iterable

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.weekly_report import WeeklyReport, WeeklyReportItem
from app.services.stock_metrics import compute_stock_status


@dataclass(frozen=True)
class ArticlePoint:
    report_date: date
    sales_qty: float
    stock_qty: float


def _normalize_article(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


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
    normalized_article = _normalize_article(article)
    history = _load_history_for_articles(db, articles=[normalized_article]).get(
        normalized_article, []
    )
    if not history:
        return {
            "article": normalized_article,
            "weeks_without_sales": 0,
            "stock_qty": 0,
            "last_sale_date": None,
        }

    weeks = 0
    last_sale_date: date | None = None
    for p in history:
        if p.sales_qty > 0:
            last_sale_date = p.report_date
            break
        weeks += 1

    latest_stock = history[0].stock_qty
    return {
        "article": normalized_article,
        "weeks_without_sales": weeks,
        "stock_qty": latest_stock,
        "last_sale_date": str(last_sale_date) if last_sale_date else None,
    }


def get_seasonality(db: Session, *, article: str) -> dict[str, Any]:
    normalized_article = _normalize_article(article)
    history = _load_history_for_articles(db, articles=[normalized_article]).get(
        normalized_article, []
    )
    month_sales: dict[int, float] = defaultdict(float)
    for p in history:
        month_sales[p.report_date.month] += p.sales_qty

    if not month_sales:
        return {
            "article": normalized_article,
            "monthly_sales": {},
            "peak_month": None,
            "seasonality_coef": 0.0,
        }

    peak_month = max(month_sales.items(), key=lambda x: x[1])[0] if month_sales else None
    non_zero = [v for v in month_sales.values() if v > 0]
    avg = (sum(non_zero) / len(non_zero)) if non_zero else 0.0
    peak_val = month_sales.get(peak_month, 0.0) if peak_month else 0.0
    coef = (peak_val / avg) if avg > 0 else 0.0

    return {
        "article": normalized_article,
        "monthly_sales": {str(k): float(v) for k, v in sorted(month_sales.items())},
        "peak_month": peak_month,
        "seasonality_coef": round(float(coef), 4),
    }


def get_trend(db: Session, *, article: str, weeks: int = 4) -> dict[str, Any]:
    normalized_article = _normalize_article(article)
    history = _load_history_for_articles(db, articles=[normalized_article]).get(
        normalized_article, []
    )
    points = history[: max(2, int(weeks))]
    sales = [p.sales_qty for p in points]

    if len(sales) < 2:
        return {
            "article": normalized_article,
            "trend": "flat",
            "pct_change": 0.0,
            "series": sales,
        }

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
        "article": normalized_article,
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
    db: Session,
    *,
    limit: int = 200,
    offset: int = 0,
    q: str | None = None,
    group1: str | None = None,
    group2: str | None = None,
    group3: str | None = None,
    kpi_filter: str | None = None,
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

    q_norm = (q or "").strip()
    g1 = (group1 or "").strip()
    g2 = (group2 or "").strip()
    g3 = (group3 or "").strip()
    kpi_norm = (kpi_filter or "all").strip() or "all"
    has_any_filter = bool(q_norm or g1 or g2 or g3 or (kpi_norm != "all"))

    # ── Базовый запрос по последнему отчёту (для rows + KPI) ─────────────
    base_query = (
        select(
            WeeklyReportItem.article,
            WeeklyReportItem.name,
            WeeklyReportItem.group1,
            WeeklyReportItem.group2,
            WeeklyReportItem.group3,
            WeeklyReportItem.price_category,
            WeeklyReportItem.stock_qty,
            WeeklyReportItem.sales_qty,
            WeeklyReportItem.store_price,
            WeeklyReportItem.actual_margin_pct,
        )
        .where(WeeklyReportItem.report_id == latest.id)
    )

    if g1:
        base_query = base_query.where(WeeklyReportItem.group1 == g1)
    if g2:
        base_query = base_query.where(WeeklyReportItem.group2 == g2)
    if g3:
        base_query = base_query.where(WeeklyReportItem.group3 == g3)
    if q_norm:
        like = f"%{q_norm}%"
        base_query = base_query.where(
            or_(
                WeeklyReportItem.article.ilike(like),
                WeeklyReportItem.name.ilike(like),
                WeeklyReportItem.group1.ilike(like),
                WeeklyReportItem.group2.ilike(like),
                WeeklyReportItem.group3.ilike(like),
                WeeklyReportItem.price_category.ilike(like),
            )
        )

    # KPI-фильтры (кроме seasonal) можно применить на SQL уровне
    if kpi_norm == "in_stock":
        base_query = base_query.where(
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
        )
    elif kpi_norm == "low_critical":
        base_query = base_query.where(
            WeeklyReportItem.sales_qty.is_not(None),
            WeeklyReportItem.sales_qty > 0,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
            WeeklyReportItem.stock_qty < (WeeklyReportItem.sales_qty * 2.0),
        )

    items_query = base_query.order_by(WeeklyReportItem.article.asc()).limit(limit).offset(offset)
    items = db.execute(items_query).all()
    # ── Продажи из предыдущего отчёта для сравнения ─────────────────────
    prev_sales_map: dict[str, float] = {}
    if previous:
        prev_rows = db.execute(
            select(WeeklyReportItem.article, WeeklyReportItem.sales_qty)
            .where(WeeklyReportItem.report_id == previous.id)
        ).all()
        for art, sq in prev_rows:
            if art:
                prev_sales_map[_normalize_article(art)] = _to_float(sq)

    # ── Добавляем пропавшие товары из недавней истории ───────────────────
    # Если товар раньше продавался, но отсутствует в текущем отчёте,
    # показываем его как позицию с нулевым остатком (не теряем сезонные SKU).
    missing_rows: list[tuple] = []
    missing_budget = max(0, limit - len(items)) if offset == 0 else 0
    allow_missing = (not has_any_filter) and (offset == 0) and (missing_budget > 0)
    if allow_missing:
        latest_articles_set = {
            _normalize_article(a[0])
            for a in db.execute(
                select(WeeklyReportItem.article).where(WeeklyReportItem.report_id == latest.id)
            ).all()
            if _normalize_article(a[0])
        }
        lookback_from = latest.report_date - timedelta(days=400)
        history_candidates = db.execute(
            select(
                WeeklyReportItem.article,
                WeeklyReportItem.name,
                WeeklyReportItem.group1,
                WeeklyReportItem.group2,
                WeeklyReportItem.group3,
                WeeklyReportItem.price_category,
                WeeklyReportItem.stock_qty,
                WeeklyReportItem.sales_qty,
                WeeklyReportItem.store_price,
                WeeklyReportItem.actual_margin_pct,
                WeeklyReport.report_date,
            )
            .join(WeeklyReport, WeeklyReport.id == WeeklyReportItem.report_id)
            .where(
                WeeklyReport.report_date < latest.report_date,
                WeeklyReport.report_date >= lookback_from,
                WeeklyReportItem.sales_qty.is_not(None),
                WeeklyReportItem.sales_qty > 0,
            )
            .order_by(WeeklyReportItem.article.asc(), WeeklyReport.report_date.desc())
        ).all()

        latest_known_by_article: dict[str, tuple] = {}
        for row in history_candidates:
            article = _normalize_article(row[0])
            if not article or article in latest_articles_set:
                continue
            if article in latest_known_by_article:
                continue
            latest_known_by_article[article] = row

        for row in latest_known_by_article.values():
            if len(missing_rows) >= missing_budget:
                break
            missing_rows.append(
                (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    0.0,
                    row[7],
                    row[8],
                    row[9],
                )
            )

    items_for_view = list(items) + missing_rows

    # ── История для тренда/сезонности ───────────────────────────────────
    articles = [_normalize_article(a) for a, *_ in items_for_view if _normalize_article(a)]
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

    # ── KPI (с учётом server-side фильтров групп/поиска) ─────────────────
    total_all_sku = db.execute(
        select(func.count()).where(WeeklyReportItem.report_id == latest.id)
    ).scalar_one()

    kpi_base = select(func.count()).where(WeeklyReportItem.report_id == latest.id)
    if g1:
        kpi_base = kpi_base.where(WeeklyReportItem.group1 == g1)
    if g2:
        kpi_base = kpi_base.where(WeeklyReportItem.group2 == g2)
    if g3:
        kpi_base = kpi_base.where(WeeklyReportItem.group3 == g3)
    if q_norm:
        like = f"%{q_norm}%"
        kpi_base = kpi_base.where(
            or_(
                WeeklyReportItem.article.ilike(like),
                WeeklyReportItem.name.ilike(like),
                WeeklyReportItem.group1.ilike(like),
                WeeklyReportItem.group2.ilike(like),
                WeeklyReportItem.group3.ilike(like),
                WeeklyReportItem.price_category.ilike(like),
            )
        )

    total_sku = db.execute(kpi_base).scalar_one()

    in_stock = db.execute(
        kpi_base.where(
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
        )
    ).scalar_one()

    low_critical = db.execute(
        kpi_base.where(
            WeeklyReportItem.sales_qty.is_not(None),
            WeeklyReportItem.sales_qty > 0,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
            WeeklyReportItem.stock_qty < (WeeklyReportItem.sales_qty * 2.0),
        )
    ).scalar_one()

    critical = db.execute(
        kpi_base.where(
            WeeklyReportItem.sales_qty.is_not(None),
            WeeklyReportItem.sales_qty > 0,
            WeeklyReportItem.stock_qty.is_not(None),
            WeeklyReportItem.stock_qty > 0,
            WeeklyReportItem.stock_qty <= WeeklyReportItem.sales_qty,
        )
    ).scalar_one()

    def _is_preseason_window(*, current_month: int, peak_month: int) -> bool:
        months_before_peak = (peak_month - current_month) % 12
        return months_before_peak in (1, 2)

    def _is_sharp_seasonal_peak(month_sales: dict[int, float], peak_month: int) -> bool:
        peak_val = month_sales.get(peak_month, 0.0)
        non_peak_vals = [v for m, v in month_sales.items() if m != peak_month and v > 0]
        if peak_val <= 0 or len(non_peak_vals) < 2:
            return False

        baseline = sum(non_peak_vals) / len(non_peak_vals)
        # Должен быть выраженный всплеск, а не просто случайный максимум.
        if baseline <= 0:
            return False
        return peak_val >= baseline * 2.0

    # ── Сезонность KPI должна быть общей, не "по странице" ───────────────
    # Считаем по всем артикулам, попавшим под поисковые/групповые фильтры.
    seasonal_count_total = 0
    try:
        seasonal_articles = [
            _normalize_article(a)
            for (a,) in db.execute(
                select(WeeklyReportItem.article)
                .where(WeeklyReportItem.report_id == latest.id)
                .where(
                    WeeklyReportItem.group1 == g1 if g1 else True,
                    WeeklyReportItem.group2 == g2 if g2 else True,
                    WeeklyReportItem.group3 == g3 if g3 else True,
                )
            ).all()
        ]
        if q_norm:
            like = f"%{q_norm}%"
            seasonal_articles = [
                _normalize_article(a)
                for (a,) in db.execute(
                    select(WeeklyReportItem.article)
                    .where(WeeklyReportItem.report_id == latest.id)
                    .where(
                        or_(
                            WeeklyReportItem.article.ilike(like),
                            WeeklyReportItem.name.ilike(like),
                            WeeklyReportItem.group1.ilike(like),
                            WeeklyReportItem.group2.ilike(like),
                            WeeklyReportItem.group3.ilike(like),
                            WeeklyReportItem.price_category.ilike(like),
                        )
                    )
                ).all()
            ]
        seasonal_articles = [a for a in set(seasonal_articles) if a]
        if seasonal_articles:
            hist_all = _load_history_for_articles(db, articles=seasonal_articles)
            current_month = latest.report_date.month
            for art in seasonal_articles:
                hist = hist_all.get(art, [])
                month_sales: dict[int, float] = defaultdict(float)
                for p in hist:
                    month_sales[p.report_date.month] += p.sales_qty
                peak_month = (
                    max(month_sales.items(), key=lambda x: x[1])[0] if month_sales else None
                )
                if peak_month is None or len(month_sales) < 4:
                    continue
                if _is_sharp_seasonal_peak(month_sales, peak_month) and _is_preseason_window(
                    current_month=current_month, peak_month=peak_month
                ):
                    seasonal_count_total += 1
    except Exception:
        # KPI сезонности не должен ломать страницу
        seasonal_count_total = 0

    # ── Строки таблицы ───────────────────────────────────────────────────
    rows: list[dict[str, Any]] = []
    current_month = latest.report_date.month

    for article, name, group1, group2, group3, price_category, stock_qty, sales_qty, store_price, margin_pct in items_for_view:
        art = _normalize_article(article)
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
        is_seasonal = False
        if peak_month is not None and len(month_sales) >= 4:
            if _is_sharp_seasonal_peak(month_sales, peak_month):
                is_seasonal = _is_preseason_window(
                    current_month=current_month,
                    peak_month=peak_month,
                )
        # Критический порог: для новых позиций уменьшаем горизонт покрытия,
        # чтобы не завышать рекомендацию при слабой истории.
        s = _to_float(sales_qty)
        st = _to_float(stock_qty)
        history_len = len(hist)
        cover_weeks = 4 if history_len >= 4 else 3 if history_len >= 2 else 2
        critical_threshold = s * cover_weeks
        recommended_order_qty = max(0, math.ceil(critical_threshold - st))
        prev_s_val = prev_sales_map.get(art, None)

        # Продажи в день (≈ sales_qty / 7)
        sales_per_day = round(s / 7, 1) if s > 0 else 0.0

        # Статус остатка
        stock_status = compute_stock_status(stock_qty=st, sales_qty=s)

        rows.append(
            {
                "article": art,
                "name": name or "",
                "group1": group1 or "",
                "group2": group2 or "",
                "group3": group3 or "",
                "price_category": price_category or "",
                "stock_qty": st,
                "sales_qty": s,
                "sales_per_day": sales_per_day,
                "prev_sales_qty": prev_s_val,
                "store_price": _to_float(store_price),
                "actual_margin_pct": _to_float(margin_pct),
                "critical_threshold": round(critical_threshold),
                "recommended_order_qty": int(recommended_order_qty),
                "stock_status": stock_status,       # ok / low / critical / empty
                "weeks_without_sales": weeks_wo,
                "last_sale_date": str(last_sale_date) if last_sale_date else None,
                "peak_month": peak_month,
                "is_seasonal": is_seasonal,
                "trend": trend,
                "trend_pct": round(float(pct), 2),
            }
        )

    # ── KPI-фильтр seasonal применяем на уровне page-rows ─────────────────
    if kpi_norm == "seasonal":
        rows = [r for r in rows if r.get("is_seasonal")]

    return {
        "report_date": str(latest.report_date),
        "prev_report_date": str(previous.report_date) if previous else None,
        "kpi": {
            "total_sku": int(total_sku),
            "total_all_sku": int(total_all_sku),
            "in_stock": int(in_stock),
            "low_critical_count": int(low_critical),
            "critical_count": int(critical),
            "seasonal_count": int(seasonal_count_total),
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

    # Статус остатка — единая логика для всего приложения.
    stock_status = compute_stock_status(stock_qty=stock_qty, sales_qty=sales_qty)

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
