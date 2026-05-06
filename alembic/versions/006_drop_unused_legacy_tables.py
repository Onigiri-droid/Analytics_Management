"""drop unused legacy tables

Revision ID: 006_drop_unused_legacy_tables
Revises: 005_account_activation_tokens
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_drop_unused_legacy_tables"
down_revision: Union[str, None] = "005_account_activation_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind: sa.engine.Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    # Drop in reverse-creation order to keep operation predictable.
    for table_name, index_name in (
        ("product_snapshots", "ix_product_snapshots_id"),
        ("uploads", "ix_uploads_id"),
        ("products", "ix_products_id"),
    ):
        if _table_exists(bind, table_name):
            op.drop_index(index_name, table_name=table_name)
            op.drop_table(table_name)


def downgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_products_id"), "products", ["id"], unique=False)

    op.create_table(
        "uploads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_uploads_id"), "uploads", ["id"], unique=False)

    op.create_table(
        "product_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_product_snapshots_id"), "product_snapshots", ["id"], unique=False)
