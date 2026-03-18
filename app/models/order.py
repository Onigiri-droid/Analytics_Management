# app/models/order.py
from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class OrderItem(Base):
    __tablename__ = "order_items"

    id          = Column(Integer, primary_key=True, index=True)
    article     = Column(String, nullable=False, unique=True, index=True)
    name        = Column(String, nullable=True)
    qty         = Column(Float, nullable=False, default=0.0)
    store_price = Column(Float, nullable=True)
    added_at    = Column(DateTime(timezone=True), server_default=func.now())