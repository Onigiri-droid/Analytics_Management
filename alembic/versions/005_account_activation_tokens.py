"""add account activation tokens

Revision ID: 005_account_activation_tokens
Revises: 004_weekly_item_indexes
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005_account_activation_tokens"
down_revision: Union[str, None] = "004_weekly_item_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_activation_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_activation_tokens_id"),
        "account_activation_tokens",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_activation_tokens_user_id"),
        "account_activation_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_activation_tokens_token_hash"),
        "account_activation_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_account_activation_tokens_token_hash"),
        table_name="account_activation_tokens",
    )
    op.drop_index(
        op.f("ix_account_activation_tokens_user_id"),
        table_name="account_activation_tokens",
    )
    op.drop_index(op.f("ix_account_activation_tokens_id"), table_name="account_activation_tokens")
    op.drop_table("account_activation_tokens")
