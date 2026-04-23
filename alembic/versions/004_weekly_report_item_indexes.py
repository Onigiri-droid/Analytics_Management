"""add indexes for weekly_report_items article lookups

Revision ID: 004_weekly_item_indexes
Revises: 003_auth_users
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op


revision: str = "004_weekly_item_indexes"
down_revision: Union[str, None] = "003_auth_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_weekly_report_items_article",
        "weekly_report_items",
        ["article"],
        unique=False,
    )
    op.create_index(
        "ix_weekly_report_items_report_article",
        "weekly_report_items",
        ["report_id", "article"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_weekly_report_items_report_article", table_name="weekly_report_items")
    op.drop_index("ix_weekly_report_items_article", table_name="weekly_report_items")
