"""Weekly reports and items

Revision ID: 002
Revises: 001
Create Date: 2026-02-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "weekly_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("upload_date", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_weekly_reports_id"), "weekly_reports", ["id"], unique=False)
    op.create_index(op.f("ix_weekly_reports_report_date"), "weekly_reports", ["report_date"], unique=False)

    op.create_table(
        "weekly_report_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("article", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("group1", sa.String(), nullable=True),
        sa.Column("group2", sa.String(), nullable=True),
        sa.Column("group3", sa.String(), nullable=True),
        sa.Column("cost_price", sa.Float(), nullable=True),
        sa.Column("base_price", sa.Float(), nullable=True),
        sa.Column("store_price", sa.Float(), nullable=True),
        sa.Column("stock_qty", sa.Float(), nullable=True),
        sa.Column("sales_qty", sa.Float(), nullable=True),
        sa.Column("actual_margin_pct", sa.Float(), nullable=True),
        sa.Column("arrival_date", sa.Date(), nullable=True),
        sa.Column("price_category", sa.String(), nullable=True),
        sa.Column("price_valid_until", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["weekly_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_weekly_report_items_id"), "weekly_report_items", ["id"], unique=False)
    op.create_index(op.f("ix_weekly_report_items_report_id"), "weekly_report_items", ["report_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_weekly_report_items_report_id"), table_name="weekly_report_items")
    op.drop_index(op.f("ix_weekly_report_items_id"), table_name="weekly_report_items")
    op.drop_table("weekly_report_items")
    op.drop_index(op.f("ix_weekly_reports_report_date"), table_name="weekly_reports")
    op.drop_index(op.f("ix_weekly_reports_id"), table_name="weekly_reports")
    op.drop_table("weekly_reports")

