from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.weekly_report import WeeklyReport, WeeklyReportItem


def _parse_report_date_from_filename(filename: str) -> date:
    """
    Извлекает дату отчёта из имени файла вида 'АП_13.11.xlsx'.
    Год берём текущий, чтобы не усложнять формат.
    """
    match = re.search(r"(\d{2})\.(\d{2})", filename)
    if not match:
        raise HTTPException(status_code=400, detail="Не удалось определить дату отчёта из имени файла")
    day, month = map(int, match.groups())
    today = date.today()
    try:
        return date(today.year, month, day)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректная дата отчёта в имени файла")


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


def ingest_weekly_report(*, filename: str, file_bytes: bytes, db: Session) -> dict[str, Any]:
    """
    Создаёт WeeklyReport и WeeklyReportItem'ы из Excel.
    """
    report_date = _parse_report_date_from_filename(filename)

    # Защита от повторной загрузки
    existing = db.execute(
        select(WeeklyReport).where(WeeklyReport.report_date == report_date)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Отчёт за эту дату уже существует")

    df = _normalize_dataframe(file_bytes)

    # Создаём заголовок отчёта
    report = WeeklyReport(
        report_date=report_date,
        filename=filename,
    )
    db.add(report)
    db.flush()  # чтобы получить report.id

    items: list[WeeklyReportItem] = []
    for _, row in df.iterrows():
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
            sales_qty=_to_db_value(row["Продажа ШТ"]),
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

