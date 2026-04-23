# app\services\weekly_reports.py
from __future__ import annotations

import os
import re
from datetime import date
from io import BytesIO
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.weekly_report import WeeklyReport, WeeklyReportItem


def _normalize_year(raw_year: str | None) -> int:
    if not raw_year:
        return date.today().year
    year = int(raw_year)
    if len(raw_year) == 2:
        return 2000 + year
    return year


def _parse_report_meta_from_filename(filename: str) -> tuple[date, int, bool]:
    """
    Возвращает:
      - report_date (дата среза, обычно конец периода)
      - period_days (длительность периода в днях)
      - has_explicit_year (год явно указан в имени)

    Поддерживаемые имена:
      - 'АП_13.11.xlsx'                    -> 1 день
      - 'А.П.1-31.03.25г.xls'              -> 31 день
      - 'Report_24-30.12.2025.xlsx'        -> 7 дней
    """
    base_name = os.path.basename(filename)

    # Сначала пробуем период "с-дд.мм[.гг|.гггг]".
    range_match = re.search(
        r"(\d{1,2})\s*[-_]\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?",
        base_name,
    )
    if range_match:
        start_day, end_day, month, raw_year = range_match.groups()
        start_day_i = int(start_day)
        end_day_i = int(end_day)
        month_i = int(month)
        year_i = _normalize_year(raw_year)
        try:
            report_date = date(year_i, month_i, end_day_i)
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректная дата отчёта в имени файла")

        if end_day_i >= start_day_i:
            period_days = (end_day_i - start_day_i) + 1
        else:
            # Нестандартный диапазон -> считаем как недельный отчёт.
            period_days = 7
        return report_date, max(1, period_days), (raw_year is not None)

    # Фолбэк: одна дата "дд.мм[.гг|.гггг]".
    single_match = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", base_name)
    if not single_match:
        raise HTTPException(status_code=400, detail="Не удалось определить дату отчёта из имени файла")

    day, month, raw_year = single_match.groups()
    year_i = _normalize_year(raw_year)
    try:
        return date(year_i, int(month), int(day)), 7, (raw_year is not None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректная дата отчёта в имени файла")


def infer_report_period_from_filename(filename: str) -> dict[str, Any]:
    """
    Безопасный парсинг метаданных периода из имени файла.
    Используется в UI для корректной сортировки/подписей.
    """
    try:
        report_date, period_days, _ = _parse_report_meta_from_filename(filename)
    except HTTPException:
        return {
            "report_date": None,
            "period_days": 7,
            "period_label": "Недельный",
        }

    if period_days >= 27:
        label = "Месячный"
    elif period_days >= 13:
        label = "Периодный"
    else:
        label = "Недельный"

    return {
        "report_date": report_date,
        "period_days": period_days,
        "period_label": label,
    }


def _normalize_dataframe(raw_bytes: bytes) -> pd.DataFrame:
    """
    Читает Excel в DataFrame и приводит его к нужной структуре/типам.
    """
    df = pd.read_excel(BytesIO(raw_bytes))

    # Чистим названия колонок от пробелов
    df.columns = df.columns.str.strip()

    # Оставляем только нужные колонки в нужном порядке
    expected_cols = [
        "Артикул",
        "Номенклатура",
        "Группа1",
        "Группа2",
        "Группа3",
        "Цена с/с",
        "Цена базовая",
        "Цена маг.",
        "Склад кол.",
        "Продажа ШТ",
        "Наценка факт %",
        "Дата ввоза",
        "Категория цены",
        "Действие цен (до...)",
    ]

    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"В файле отсутствуют ожидаемые колонки: {', '.join(missing)}",
        )

    df = df[expected_cols]

    # Числовые колонки
    numeric_cols = [
        "Цена с/с",
        "Цена базовая",
        "Цена маг.",
        "Склад кол.",
        "Продажа ШТ",
        "Наценка факт %",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Даты
    date_cols = ["Дата ввоза", "Действие цен (до...)"]
    for col in date_cols:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

    # Можно отфильтровать полностью пустые строки по ключевым полям
    df = df[df["Артикул"].notna() | df["Номенклатура"].notna()]

    return df


def _to_db_value(value: Any) -> Any:
    """
    Преобразует pandas-значение в то, что понимает SQLAlchemy/PostgreSQL:
    - NaN / NaT -> None (NULL в БД)
    - остальное оставляем как есть.
    """
    if pd.isna(value):
        return None
    return value


def _infer_report_year_from_dataframe(df: pd.DataFrame) -> int | None:
    years: list[int] = []
    for col in ("Дата ввоза", "Действие цен (до...)"):
        if col not in df.columns:
            continue
        series = df[col].dropna()
        if series.empty:
            continue
        years.extend([int(v.year) for v in series if hasattr(v, "year") and v.year])

    if not years:
        return None
    year_counts = pd.Series(years).value_counts()
    return int(year_counts.index[0])


def ingest_weekly_report(*, filename: str, file_bytes: bytes, db: Session) -> dict[str, Any]:
    """
    Создаёт WeeklyReport и WeeklyReportItem'ы из Excel.
    """
    report_date, period_days, has_explicit_year = _parse_report_meta_from_filename(filename)

    df = _normalize_dataframe(file_bytes)
    if not has_explicit_year:
        inferred_year = _infer_report_year_from_dataframe(df)
        if inferred_year:
            report_date = date(inferred_year, report_date.month, report_date.day)

    # Защита от повторной загрузки
    existing = db.execute(
        select(WeeklyReport).where(WeeklyReport.report_date == report_date)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Отчёт за эту дату уже существует")

    # Создаём заголовок отчёта
    report = WeeklyReport(
        report_date=report_date,
        filename=filename,
    )
    db.add(report)
    db.flush()  # чтобы получить report.id

    items: list[WeeklyReportItem] = []
    for _, row in df.iterrows():
        raw_sales_qty = _to_db_value(row["Продажа ШТ"])
        sales_qty = None if raw_sales_qty is None else float(raw_sales_qty)
        if sales_qty is not None and period_days > 0:
            # Храним продажи в недельном эквиваленте, чтобы месячные и недельные
            # файлы были сопоставимы в общей аналитике.
            sales_qty = sales_qty * 7.0 / float(period_days)

        item = WeeklyReportItem(
            report_id=report.id,
            article=_to_db_value(row["Артикул"]),
            name=_to_db_value(row["Номенклатура"]),
            group1=_to_db_value(row["Группа1"]),
            group2=_to_db_value(row["Группа2"]),
            group3=_to_db_value(row["Группа3"]),
            cost_price=_to_db_value(row["Цена с/с"]),
            base_price=_to_db_value(row["Цена базовая"]),
            store_price=_to_db_value(row["Цена маг."]),
            stock_qty=_to_db_value(row["Склад кол."]),
            sales_qty=sales_qty,
            actual_margin_pct=_to_db_value(row["Наценка факт %"]),
            arrival_date=_to_db_value(row["Дата ввоза"]),
            price_category=_to_db_value(row["Категория цены"]),
            price_valid_until=_to_db_value(row["Действие цен (до...)"]),
        )
        items.append(item)

    db.add_all(items)
    db.commit()

    return {
        "report_id": report.id,
        "report_date": str(report.report_date),
        "filename": report.filename,
        "items_count": len(items),
        "columns": list(df.columns),
    }


def repair_report_dates_from_filenames(db: Session) -> dict[str, int]:
    """
    Исправляет report_date у уже загруженных отчётов на основании имени файла.
    Нужен для восстановления после старой логики парсинга.
    """
    reports = db.execute(
        select(WeeklyReport).order_by(WeeklyReport.id.asc())
    ).scalars().all()

    updates = 0
    skipped_conflicts = 0
    unchanged = 0

    existing_by_date = {r.report_date: r.id for r in reports if r.report_date}

    for report in reports:
        meta = infer_report_period_from_filename(report.filename or "")
        target_date = meta["report_date"]
        if not target_date:
            unchanged += 1
            continue
        if report.report_date == target_date:
            unchanged += 1
            continue

        conflict_id = existing_by_date.get(target_date)
        if conflict_id and conflict_id != report.id:
            skipped_conflicts += 1
            continue

        if report.report_date in existing_by_date:
            existing_by_date.pop(report.report_date, None)
        report.report_date = target_date
        existing_by_date[target_date] = report.id
        updates += 1

    if updates > 0:
        db.commit()

    return {
        "updated": updates,
        "unchanged": unchanged,
        "skipped_conflicts": skipped_conflicts,
    }

