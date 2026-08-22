"""add location_id to orders and backfill

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-22

Stage 1 — S1-3: orders.location_id

Добавляет location_id в orders:
  orders.location_id → locations.id ON DELETE RESTRICT

RESTRICT (не CASCADE): Location нельзя физически удалить, если существуют
исторические Orders. Soft delete (is_active=False) — единственный
допустимый способ «удалить» Location с заказами (ADR-005).

Backfill-правило:
  orders.restaurant_id → locations.restaurant_id → locations.id
  (migration 0010 гарантирует: каждый Restaurant имеет ровно одну Location)
  Берём MIN(l.id) — детерминированный выбор при >1 Location на случай
  будущей несогласованности данных.

Порядок операций:
  1. ADD COLUMN location_id BIGINT NULL
  2. BACKFILL via UPDATE
  3. VERIFY: COUNT(*) WHERE location_id IS NULL → ожидается 0
  4. VERIFY cross-brand: ORDER.restaurant_id == location.restaurant_id → ожидается 0 нарушений
  5. HALT if any violations found
  6. ALTER COLUMN location_id SET NOT NULL
  7. ADD FK fk_orders_location_id ON DELETE RESTRICT
  8. ADD INDEX ix_orders_location_id

Что НЕ делается в этой миграции:
  - НЕ удаляется orders.restaurant_id (остаётся до migration 0015)
  - НЕ изменяются waiter_calls, reservations
  - НЕ изменяется billing/quota логика

Downgrade:
  DROP INDEX ix_orders_location_id
  DROP FK fk_orders_location_id
  DROP COLUMN location_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Add location_id column (nullable first — needed for backfill) ──
    op.add_column(
        "orders",
        sa.Column(
            "location_id",
            sa.BigInteger(),
            nullable=True,  # temporarily nullable; SET NOT NULL after backfill
        ),
    )

    # ── 2. Backfill location_id via restaurant_id → locations.restaurant_id ─
    #
    # migration 0010 guarantees: each restaurant has exactly 1 location.
    # MIN(l.id) ensures deterministic pick if ever >1 (safety net).
    op.execute(sa.text("""
        UPDATE orders o
        SET location_id = (
            SELECT l.id
            FROM locations l
            WHERE l.restaurant_id = o.restaurant_id
            ORDER BY l.id
            LIMIT 1
        )
        WHERE o.location_id IS NULL
    """))

    # ── 3. Verify backfill: 0 rows with location_id IS NULL expected ───────
    result = op.get_bind().execute(sa.text("""
        SELECT COUNT(*) FROM orders WHERE location_id IS NULL
    """))
    null_count = result.scalar()
    if null_count != 0:
        raise RuntimeError(
            f"S1-3 backfill failed: {null_count} orders rows have "
            f"location_id IS NULL after backfill. "
            f"Ensure migration 0010 ran first: alembic upgrade 0010. "
            f"Check that every restaurant_id in orders has a matching "
            f"entry in locations."
        )

    # ── 4. Cross-brand consistency check ────────────────────────────────────
    #
    # After backfill: orders.restaurant_id MUST match location.restaurant_id.
    # Any mismatch indicates data corruption and must halt migration.
    result = op.get_bind().execute(sa.text("""
        SELECT COUNT(*)
        FROM orders o
        JOIN locations l ON l.id = o.location_id
        WHERE o.restaurant_id != l.restaurant_id
    """))
    cross_brand = result.scalar()
    if cross_brand != 0:
        raise RuntimeError(
            f"S1-3 cross-brand consistency check failed: {cross_brand} orders "
            f"have location_id pointing to a location of a different restaurant. "
            f"Manual inspection required before proceeding. "
            f"Query: SELECT o.id, o.restaurant_id, l.restaurant_id AS loc_restaurant "
            f"FROM orders o JOIN locations l ON l.id = o.location_id "
            f"WHERE o.restaurant_id != l.restaurant_id LIMIT 20;"
        )

    # ── 5. Set NOT NULL now that backfill is verified ──────────────────────
    op.alter_column("orders", "location_id", nullable=False)

    # ── 6. Foreign key: location_id → locations.id ON DELETE RESTRICT ──────
    #
    # RESTRICT (not CASCADE): historical orders must survive.
    # Physical deletion of a Location with existing orders is forbidden.
    # Soft delete (is_active=False) is the only allowed deactivation method.
    op.create_foreign_key(
        "fk_orders_location_id",
        "orders",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ── 7. Index for FK (hot path: orders for a location) ──────────────────
    op.create_index(
        "ix_orders_location_id",
        "orders",
        ["location_id"],
    )


def downgrade() -> None:
    # ── Remove index ───────────────────────────────────────────────────────
    op.drop_index(
        "ix_orders_location_id",
        table_name="orders",
    )

    # ── Remove FK constraint ───────────────────────────────────────────────
    op.drop_constraint(
        "fk_orders_location_id",
        "orders",
        type_="foreignkey",
    )

    # ── Drop location_id column ────────────────────────────────────────────
    op.drop_column("orders", "location_id")
