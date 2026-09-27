"""phase14_delivery_zones

Phase 14: Create delivery_zones table.

Named delivery zones per Location. Simple area-based zones (no PostGIS).
Admin manages zones (name, fee, min_order, eta_minutes).
Customer selects zone at checkout; server re-validates ownership.
fee/min_order snapshotted into Order at checkout — never mutates historical Orders.

ON DELETE RESTRICT on location_id: location cannot be deleted while zones exist.
Soft-delete via is_active=False (admin cannot delete active zones directly).

Revision ID: 0026
Revises: 0025
Create Date: 2026-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_zones",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "location_id",
            sa.BigInteger(),
            sa.ForeignKey("locations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("fee", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eta_minutes", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("fee >= 0", name="ck_dz_fee_nonneg"),
        sa.CheckConstraint("min_order >= 0", name="ck_dz_min_order_nonneg"),
        sa.CheckConstraint(
            "eta_minutes IS NULL OR eta_minutes > 0",
            name="ck_dz_eta_positive",
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_dz_sort_order_nonneg"),
    )
    op.create_index(
        "ix_dz_location_active",
        "delivery_zones",
        ["location_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_dz_location_active", table_name="delivery_zones")
    op.drop_table("delivery_zones")
