# app/routers/orders.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from openpyxl import Workbook
from io import BytesIO

from app.core.database import get_db
from app.core.templates import templates
from app.models.order import OrderItem          # ← модель теперь в models/
from app.models.weekly_report import WeeklyReport, WeeklyReportItem


router = APIRouter()


class AddOrderItem(BaseModel):
    article:     str
    name:        str = ""
    qty:         float = 0.0
    store_price: float | None = None


class RemoveOrderItem(BaseModel):
    article: str


class ExportExcelRequest(BaseModel):
    articles: list[str] = []


@router.get("/", response_class=HTMLResponse)
def orders_page(request: Request, db: Session = Depends(get_db)):
    items = db.execute(
        select(OrderItem).order_by(OrderItem.added_at.desc())
    ).scalars().all()

    rows: list[dict] = []
    grand_total = 0.0

    total_positions_to_order = len(items)
    total_qty = 0.0
    critical_positions = 0

    latest_report = db.execute(
        select(WeeklyReport).order_by(desc(WeeklyReport.report_date)).limit(1)
    ).scalar_one_or_none()

    articles = [str(i.article) for i in items if i.article]
    latest_items_by_article: dict[str, dict] = {}
    if latest_report and articles:
        latest_rows = db.execute(
            select(
                WeeklyReportItem.article,
                WeeklyReportItem.name,
                WeeklyReportItem.group1,
                WeeklyReportItem.stock_qty,
                WeeklyReportItem.sales_qty,
            )
            .where(
                WeeklyReportItem.report_id == latest_report.id,
                WeeklyReportItem.article.in_(articles),
            )
        ).all()

        for art, name, group1, stock_qty, sales_qty in latest_rows:
            a = str(art)
            latest_items_by_article[a] = {
                "name": name or "",
                "category": group1 or "",
                "stock_qty": float(stock_qty or 0),
                "sales_qty": float(sales_qty or 0),
            }

    def compute_stock_status(stock_qty: float, sales_qty: float) -> str:
        # Совпадает с логикой на странице остатков.
        if sales_qty > 0 and stock_qty > 0:
            ratio = stock_qty / sales_qty
            if ratio < 1:
                return "critical"
            if ratio < 2:
                return "low"
            return "ok"
        if stock_qty == 0:
            return "empty"
        return "ok"

    for i in items:
        article = str(i.article)
        latest = latest_items_by_article.get(article, {})

        name = i.name or latest.get("name") or ""
        category = latest.get("category") or ""
        stock_qty = float(latest.get("stock_qty") or 0)
        sales_qty = float(latest.get("sales_qty") or 0)

        stock_status = compute_stock_status(stock_qty=stock_qty, sales_qty=sales_qty)
        min_order_qty = int(round(sales_qty * 4)) if sales_qty > 0 else 0

        qty = float(i.qty or 0)
        store_price = float(i.store_price or 0) if i.store_price is not None else None
        total = round(qty * float(store_price or 0), 2)

        rows.append(
            {
                "article": article,
                "name": name,
                "category": category,
                "qty": qty,
                "store_price": store_price,
                "stock_qty": stock_qty,
                "stock_status": stock_status,
                "min_order_qty": min_order_qty,
                "total": total,
            }
        )
        total_qty += qty
        grand_total += total
        if stock_status == "critical":
            critical_positions += 1

    return templates.TemplateResponse(
        "orders.html",
        {
            "request":     request,
            "active_page": "orders",
            "rows":        rows,
            "grand_total": grand_total,
            "total_positions_to_order": total_positions_to_order,
            "total_qty": total_qty,
            "critical_positions": critical_positions,
        },
    )


@router.post("/add")
def add_to_order(payload: AddOrderItem, db: Session = Depends(get_db)):
    existing = db.execute(
        select(OrderItem).where(OrderItem.article == payload.article)
    ).scalar_one_or_none()

    if existing:
        existing.qty += payload.qty if payload.qty > 0 else 0
        if payload.store_price is not None:
            existing.store_price = payload.store_price
    else:
        db.add(OrderItem(
            article=payload.article,
            name=payload.name,
            qty=payload.qty,
            store_price=payload.store_price,
        ))

    db.commit()
    return JSONResponse({"status": "ok", "article": payload.article})


@router.post("/remove")
def remove_from_order(payload: RemoveOrderItem, db: Session = Depends(get_db)):
    item = db.execute(
        select(OrderItem).where(OrderItem.article == payload.article)
    ).scalar_one_or_none()
    if item:
        db.delete(item)
        db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/clear")
def clear_order(db: Session = Depends(get_db)):
    for item in db.execute(select(OrderItem)).scalars().all():
        db.delete(item)
    db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/export/excel")
def export_orders_excel(payload: ExportExcelRequest, db: Session = Depends(get_db)):
    # Берём только выбранные позиции (если пришёл список).
    q = select(OrderItem).order_by(OrderItem.added_at.desc())
    if payload.articles:
        q = q.where(OrderItem.article.in_(payload.articles))
    items = db.execute(q).scalars().all()

    if not items:
        # Возвращаем пустой Excel (чтобы frontend не падал).
        wb = Workbook()
        ws = wb.active
        ws.title = "Заказ"
        ws.append(["Артикул", "Наименование", "Категория", "Остаток", "К заказу (шт)", "Цена (₽)", "Сумма (₽)"])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=order.xlsx"},
        )

    articles = [str(i.article) for i in items if i.article]

    def compute_stock_status(stock_qty: float, sales_qty: float) -> str:
        if sales_qty > 0 and stock_qty > 0:
            ratio = stock_qty / sales_qty
            if ratio < 1:
                return "critical"
            if ratio < 2:
                return "low"
            return "ok"
        if stock_qty == 0:
            return "empty"
        return "ok"

    latest_report = db.execute(
        select(WeeklyReport).order_by(desc(WeeklyReport.report_date)).limit(1)
    ).scalar_one_or_none()

    latest_items_by_article: dict[str, dict] = {}
    if latest_report and articles:
        latest_rows = db.execute(
            select(
                WeeklyReportItem.article,
                WeeklyReportItem.name,
                WeeklyReportItem.group1,
                WeeklyReportItem.stock_qty,
                WeeklyReportItem.sales_qty,
            )
            .where(
                WeeklyReportItem.report_id == latest_report.id,
                WeeklyReportItem.article.in_(articles),
            )
        ).all()

        for art, name, group1, stock_qty, sales_qty in latest_rows:
            latest_items_by_article[str(art)] = {
                "name": name or "",
                "category": group1 or "",
                "stock_qty": float(stock_qty or 0),
                "sales_qty": float(sales_qty or 0),
            }

    wb = Workbook()
    ws = wb.active
    ws.title = "Заказ"

    ws.append(["Артикул", "Наименование", "Категория", "Остаток", "К заказу (шт)", "Цена (₽)", "Сумма (₽)"])

    for i in items:
        article = str(i.article)
        latest = latest_items_by_article.get(article, {})

        name = i.name or latest.get("name") or ""
        category = latest.get("category") or ""
        stock_qty = float(latest.get("stock_qty") or 0)
        sales_qty = float(latest.get("sales_qty") or 0)
        stock_status = compute_stock_status(stock_qty=stock_qty, sales_qty=sales_qty)

        qty = float(i.qty or 0)
        store_price = float(i.store_price or 0) if i.store_price is not None else None
        price_val = float(store_price or 0)
        total = round(qty * price_val, 2)

        # Остаток показываем числом (как в таблице), статус не дублируем отдельной колонкой.
        ws.append([
            article,
            name,
            category,
            stock_qty,
            int(round(qty)) if qty.is_integer() else qty,
            price_val,
            total,
        ])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=order.xlsx"},
    )