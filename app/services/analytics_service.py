# app/services/analytics_service.py
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import math
from typing import Any, Iterable

from sqlalchemy import String, cast, desc, func, select
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.weekly_report import WeeklyReport, WeeklyReportItem
from app.services.stock_metrics import compute_stock_status
from app.services.weekly_reports import infer_report_period_from_filename


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


def _format_week_label(start_date: date, end_date: date) -> str:
    months_short = {
        1: "янв", 2: "фев", 3: "мар", 4: "апр",
        5: "май", 6: "июн", 7: "июл", 8: "авг",
        9: "сен", 10: "окт", 11: "ноя", 12: "дек",
    }
    return f"{start_date.day}–{end_date.day} {months_short.get(end_date.month, str(end_date.month))}"


def _build_weekly_sales_points_for_article(
    db: Session, *, article: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = db.execute(
        select(
            WeeklyReport.report_date,
            WeeklyReport.filename,
            WeeklyReportItem.sales_qty,
        )
        .join(WeeklyReportItem, WeeklyReport.id == WeeklyReportItem.report_id)
        .where(WeeklyReportItem.article == article)
        .order_by(WeeklyReport.report_date.asc())
    ).all()

    if not rows:
        return [], {"selected_month_key": None, "selected_month_title": None}

    weekly_points: list[dict[str, Any]] = []
    monthly_rows: list[tuple[date, str, float, int]] = []
    months_with_real_weeks: set[tuple[int, int]] = set()

    for report_date, filename, sales_qty in rows:
        meta = infer_report_period_from_filename(filename or "")
        period_days = int(meta.get("period_days") or 7)
        sales = _to_float(sales_qty)
        source_date = report_date

        # В БД sales_qty уже недельный эквивалент. Восстанавливаем продажи
        # исходного периода, чтобы корректно сравнивать daily между периодами.
        raw_period_sales = sales * (period_days / 7.0)

        if period_days <= 10:
            date_to = source_date
            date_from = date_to - timedelta(days=max(1, period_days) - 1)
            mid_date = date_from + (date_to - date_from) / 2
            weekly_points.append(
                {
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                    "mid_date": mid_date.isoformat(),
                    "label": _format_week_label(date_from, date_to),
                    "sales_qty": round(float(raw_period_sales), 4),
                    "period_days": int(period_days),
                    "estimated": False,
                    "source_period_days": int(period_days),
                    "source_report_date": source_date.isoformat(),
                }
            )
            months_with_real_weeks.add((source_date.year, source_date.month))
        elif period_days >= 27:
            monthly_rows.append((source_date, filename or "", raw_period_sales, period_days))

    for source_date, _, sales, period_days in monthly_rows:
        month_key = (source_date.year, source_date.month)
        if month_key in months_with_real_weeks:
            continue

        days_in_month = calendar.monthrange(source_date.year, source_date.month)[1]
        month_days = max(1, days_in_month)
        chunks = [(1, 7), (8, 14), (15, 21), (22, month_days)]
        for start_day, end_day in chunks:
            if start_day > month_days:
                continue
            real_end = min(end_day, month_days)
            span_days = (real_end - start_day) + 1
            if span_days <= 0:
                continue
            date_from = date(source_date.year, source_date.month, start_day)
            date_to = date(source_date.year, source_date.month, real_end)
            mid_date = date_from + (date_to - date_from) / 2
            part_sales = sales * (span_days / month_days)
            weekly_points.append(
                {
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                    "mid_date": mid_date.isoformat(),
                    "label": _format_week_label(date_from, date_to),
                    "sales_qty": round(float(part_sales), 4),
                    "period_days": int(span_days),
                    "estimated": True,
                    "source_period_days": int(period_days),
                    "source_report_date": source_date.isoformat(),
                    "estimated_note": "Расчётная оценка на основе месячного отчёта",
                }
            )

    weekly_points.sort(key=lambda x: (x["mid_date"], x["date_from"]))
    last_12 = weekly_points[-12:]

    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in weekly_points:
        d_from = date.fromisoformat(p["date_from"])
        key = f"{d_from.year:04d}-{d_from.month:02d}"
        by_month[key].append(p)
    for key in list(by_month.keys()):
        by_month[key].sort(key=lambda x: (x["mid_date"], x["date_from"]))

    selected_month_key = None
    selected_month_title = None
    if last_12:
        last_point_date = date.fromisoformat(last_12[-1]["date_from"])
        candidate_keys = [
            k for k in by_month.keys() if k.endswith(f"-{last_point_date.month:02d}")
        ]
        if candidate_keys:
            selected_month_key = sorted(candidate_keys)[-1]
            y, m = selected_month_key.split("-")
            month_names = {
                1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
                5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
                9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
            }
            selected_month_title = f"{month_names.get(int(m), m)} {y}"

    return last_12, {
        "weeks_by_month": dict(by_month),
        "selected_month_key": selected_month_key,
        "selected_month_title": selected_month_title,
    }


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
    weekly_points, extra = _build_weekly_sales_points_for_article(db, article=normalized_article)
    sales = [float(p.get("sales_qty") or 0.0) for p in weekly_points]

    if len(weekly_points) < 2:
        return {
            "article": normalized_article,
            "trend": "flat",
            "pct_change": 0.0,
            "series": sales,
            "weekly_points": weekly_points,
            "weeks_by_month": extra.get("weeks_by_month", {}),
            "selected_month": None,
            "selected_month_title": extra.get("selected_month_title"),
            "is_approx": False,
            "has_gap_warning": False,
            "comparison_note": None,
        }

    prev_point = weekly_points[-2]
    last_point = weekly_points[-1]
    prev_sales = float(prev_point.get("sales_qty") or 0.0)
    last_sales = float(last_point.get("sales_qty") or 0.0)
    prev_days = max(1, int(prev_point.get("period_days") or 7))
    last_days = max(1, int(last_point.get("period_days") or 7))
    prev_daily = prev_sales / prev_days
    last_daily = last_sales / last_days

    pct = (
        ((last_daily - prev_daily) / prev_daily * 100.0)
        if prev_daily > 0
        else (100.0 if last_daily > 0 else 0.0)
    )
    trend = "up" if pct > 5 else "down" if pct < -5 else "flat"
    prev_mid = date.fromisoformat(prev_point["mid_date"])
    last_mid = date.fromisoformat(last_point["mid_date"])
    gap_days = (last_mid - prev_mid).days
    has_gap_warning = gap_days > 45
    is_approx = bool(prev_point.get("estimated") or last_point.get("estimated"))
    if prev_days != last_days:
        is_approx = True

    comparison_note = None
    if is_approx:
        comparison_note = "Приблизительное сравнение: один из периодов рассчитан на основе месячного отчёта"
    if has_gap_warning:
        comparison_note = "Разрыв в данных"

    return {
        "article": normalized_article,
        "trend": trend,
        "pct_change": round(float(pct), 2),
        "series": sales,
        "weekly_points": weekly_points,
        "weeks_by_month": extra.get("weeks_by_month", {}),
        "selected_month": (date.fromisoformat(extra["selected_month_key"] + "-01").month - 1)
        if extra.get("selected_month_key")
        else None,
        "selected_month_title": extra.get("selected_month_title"),
        "is_approx": is_approx,
        "has_gap_warning": has_gap_warning,
        "comparison_note": comparison_note,
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
    seasonal_articles_set: set[str] = set()
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
                .where(
                    or_(
                        WeeklyReportItem.article.ilike(f"%{q_norm}%"),
                        WeeklyReportItem.name.ilike(f"%{q_norm}%"),
                        WeeklyReportItem.group1.ilike(f"%{q_norm}%"),
                        WeeklyReportItem.group2.ilike(f"%{q_norm}%"),
                        WeeklyReportItem.group3.ilike(f"%{q_norm}%"),
                        WeeklyReportItem.price_category.ilike(f"%{q_norm}%"),
                    )
                    if q_norm
                    else True
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
                    seasonal_articles_set.add(art)
            seasonal_count_total = len(seasonal_articles_set)
    except Exception:
        # KPI сезонности не должен ломать страницу
        seasonal_count_total = 0
        seasonal_articles_set = set()

    # seasonal-фильтр должен применяться до пагинации, иначе страницы "пустеют".
    if kpi_norm == "seasonal":
        if seasonal_articles_set:
            base_query = base_query.where(WeeklyReportItem.article.in_(seasonal_articles_set))
        else:
            base_query = base_query.where(False)

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
    # Если товар отсутствует в текущем отчёте, показываем последнюю известную
    # запись из истории и отмечаем как "нет данных в последнем отчёте".
    missing_rows: list[tuple] = []
    missing_articles_set: set[str] = set()
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
            art = _normalize_article(row[0])
            if art:
                missing_articles_set.add(art)
            missing_rows.append(
                (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                )
            )

    items_for_view = list(items) + missing_rows

    # ── История для тренда/сезонности ───────────────────────────────────
    articles = [_normalize_article(a) for a, *_ in items_for_view if _normalize_article(a)]
    history_map = _load_history_for_articles(db, articles=articles)

    # ── Уникальные группы для фильтров (каскадная логика) ─────────────────
    # group2 зависит от выбранной group1; group3 зависит от group1/group2.
    all_groups = db.execute(
        select(
            WeeklyReportItem.group1,
            WeeklyReportItem.group2,
            WeeklyReportItem.group3,
        )
        .where(WeeklyReportItem.report_id == latest.id)
        .distinct()
    ).all()

    groups1: list[str] = sorted({v1 for v1, _, _ in all_groups if v1})

    groups2_source = all_groups
    if g1:
        groups2_source = [row for row in groups2_source if (row[0] or "") == g1]
    groups2: list[str] = sorted({v2 for _, v2, _ in groups2_source if v2})

    groups3_source = all_groups
    if g1:
        groups3_source = [row for row in groups3_source if (row[0] or "") == g1]
    if g2:
        groups3_source = [row for row in groups3_source if (row[1] or "") == g2]
    groups3: list[str] = sorted({v3 for _, _, v3 in groups3_source if v3})

    # ── KPI (с учётом server-side фильтров групп/поиска) ─────────────────
    total_all_sku = db.execute(
        select(func.count()).where(WeeklyReportItem.report_id == latest.id)
    ).scalar_one()
    normalized_article_expr = func.nullif(
        func.upper(func.btrim(cast(WeeklyReportItem.article, String))),
        "",
    )
    total_known_sku = db.execute(
        select(func.count(func.distinct(normalized_article_expr))).where(
            WeeklyReportItem.article.is_not(None),
        )
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
                "no_latest_data": art in missing_articles_set,
            }
        )

    return {
        "report_date": str(latest.report_date),
        "prev_report_date": str(previous.report_date) if previous else None,
        "kpi": {
            "total_sku": int(total_sku),
            "total_all_sku": int(total_all_sku),
            "total_known_sku": int(total_known_sku),
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

    latest_meta = infer_report_period_from_filename(latest.filename or "")
    latest_days = max(1, int(latest_meta.get("period_days") or 7))
    prev_days = 7
    if previous:
        prev_meta = infer_report_period_from_filename(previous.filename or "")
        prev_days = max(1, int(prev_meta.get("period_days") or 7))

    current_daily_sales = sales_qty / latest_days
    prev_daily_sales = (prev_sales_qty / prev_days) if prev_sales_qty > 0 else 0.0
    sales_change_pct = 0.0
    if prev_daily_sales > 0:
        sales_change_pct = ((current_daily_sales - prev_daily_sales) / prev_daily_sales) * 100.0
    else:
        sales_change_pct = 100.0 if current_daily_sales > 0 else 0.0
    sales_change_is_approx = bool(previous and latest_days != prev_days)
    sales_change_gap_warning = bool(previous and (latest.report_date - previous.report_date).days > 45)

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
        "sales_change_is_approx": sales_change_is_approx,
        "sales_change_gap_warning": sales_change_gap_warning,
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
