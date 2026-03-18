# app/routers/orders.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.templates import templates
from app.models.order import OrderItem          # ← модель теперь в models/


router = APIRouter()


class AddOrderItem(BaseModel):
    article:     str
    name:        str = ""
    qty:         float = 0.0
    store_price: float | None = None


class RemoveOrderItem(BaseModel):
    article: str


@router.get("/", response_class=HTMLResponse)
def orders_page(request: Request, db: Session = Depends(get_db)):
    items = db.execute(
        select(OrderItem).order_by(OrderItem.added_at.desc())
    ).scalars().all()

    rows = [
        {
            "article":     i.article,
            "name":        i.name or "",
            "qty":         i.qty,
            "store_price": i.store_price,
            "total":       round((i.qty or 0) * (i.store_price or 0), 2),
        }
        for i in items
    ]
    grand_total = sum(r["total"] for r in rows)

    return templates.TemplateResponse(
        "orders.html",
        {
            "request":     request,
            "active_page": "orders",
            "rows":        rows,
            "grand_total": grand_total,
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