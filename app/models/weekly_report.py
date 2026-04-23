from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(Date, nullable=False, index=True)
    upload_date = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    filename = Column(String, nullable=False)


class WeeklyReportItem(Base):
    __tablename__ = "weekly_report_items"
    __table_args__ = (
        Index("ix_weekly_report_items_article", "article"),
        Index("ix_weekly_report_items_report_article", "report_id", "article"),
    )

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("weekly_reports.id", ondelete="CASCADE"), nullable=False, index=True)

    article = Column(String, nullable=True)  # Артикул
    name = Column(String, nullable=True)  # Номенклатура
    group1 = Column(String, nullable=True)
    group2 = Column(String, nullable=True)
    group3 = Column(String, nullable=True)

    cost_price = Column(Float, nullable=True)  # Цена с/с
    base_price = Column(Float, nullable=True)  # Цена базовая
    store_price = Column(Float, nullable=True)  # Цена маг.

    stock_qty = Column(Float, nullable=True)  # Склад кол.
    sales_qty = Column(Float, nullable=True)  # Продажа ШТ

    actual_margin_pct = Column(Float, nullable=True)  # Наценка факт %

    arrival_date = Column(Date, nullable=True)  # Дата ввоза
    price_category = Column(String, nullable=True)  # Категория цены
    price_valid_until = Column(Date, nullable=True)  # Действие цен (до...)

