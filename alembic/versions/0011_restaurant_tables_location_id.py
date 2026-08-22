"""add location_id to restaurant_tables and backfill

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-21

Stage 1 — S1-2: restaurant_tables.location_id

Добавляет location_id в restaurant_tables:
  restaurant_tables.location_id → locations.id ON DELETE CASCADE

Backfill-правило:
  restaurant_tables.restaurant_id → locations.restaurant_id
  (1 Location per Restaurant создана в migration 0010)

После backfill:
  NOT NULL constraint
  DROP uq_table_restaurant_number  ← УДАЛЯЕТСЯ в этой миграции
  CREATE UNIQUE(location_id, table_number) = uq_table_location_number

Это разрешает:
  Brand A / Location A1 → table_number="5"  ✓
  Brand A / Location A2 → table_number="5"  ✓  (разные Location)
  Brand A / Location A1 → table_number="5"  ✗  (дубль в одной Location → 409)

Legacy:
  restaurant_id остаётся — будет удалён в migration 0015.

Downgrade:
  DROP uq_table_location_number
  DROP FK fk_restaurant_tables_location_id
  DROP INDEX ix_restaurant_tables_location_id
  DROP COLUMN location_id
  RESTORE uq_table_restaurant_number
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Add location_id column (nullable first — needed for backfill) ──
    op.add_column(
        "restaurant_tables",
        sa.Column(
            "location_id",
            sa.BigInteger(),
            nullable=True,  # temporarily nullable; set NOT NULL after backfill
        ),
    )

    # ── 2. Backfill location_id via restaurant_id → locations.restaurant_id ─
    #
    # migration 0010 guarantees: each restaurant has exactly 1 location.
    # ORDER BY locations.id ensures deterministic pick if ever >1 (safety).
    op.execute(sa.text("""
        UPDATE restaurant_tables rt
        SET location_id = (
            SELECT l.id
            FROM locations l
            WHERE l.restaurant_id = rt.restaurant_id
            ORDER BY l.id
            LIMIT 1
        )
        WHERE rt.location_id IS NULL
    """))

    # ── 3. Verify backfill: 0 rows with location_id IS NULL expected ───────
    result = op.get_bind().execute(sa.text("""
        SELECT COUNT(*)
        FROM restaurant_tables
        WHERE location_id IS NULL
    """))
    null_count = result.scalar()
    if null_count != 0:
        raise RuntimeError(
            f"S1-2 backfill failed: {null_count} restaurant_tables rows "
            f"have location_id IS NULL after backfill. "
            f"Ensure migration 0010 ran first: alembic upgrade 0010"
        )

    # ── 4. Set NOT NULL now that backfill is verified ──────────────────────
    op.alter_column("restaurant_tables", "location_id", nullable=False)

    # ── 5. Foreign key: location_id → locations.id ON DELETE CASCADE ───────
    op.create_foreign_key(
        "fk_restaurant_tables_location_id",
        "restaurant_tables",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # ── 6. Index for FK (hot path: tables for a location) ──────────────────
    op.create_index(
        "ix_restaurant_tables_location_id",
        "restaurant_tables",
        ["location_id"],
    )

    # ── 7. DROP legacy uq_table_restaurant_number ──────────────────────────
    #
    # This constraint scoped uniqueness to (restaurant_id, table_number).
    # That is WRONG for the multi-location model:
    #   Brand A / Location A1 → "5"  }  both valid — same brand, diff location
    #   Brand A / Location A2 → "5"  }
    # The old constraint would block this. It must be dropped now.
    op.drop_constraint(
        "uq_table_restaurant_number",
        "restaurant_tables",
        type_="unique",
    )

    # ── 8. CREATE new UNIQUE(location_id, table_number) ────────────────────
    #
    # Correct scope: unique within a Location.
    # Same table_number in different Locations of the same Brand → allowed.
    op.create_unique_constraint(
        "uq_table_location_number",
        "restaurant_tables",
        ["location_id", "table_number"],
    )

    # ── 9. Cross-brand consistency check ───────────────────────────────────
    result = op.get_bind().execute(sa.text("""
        SELECT COUNT(*)
        FROM restaurant_tables rt
        JOIN locations l ON l.id = rt.location_id
        WHERE rt.restaurant_id != l.restaurant_id
    """))
    cross_brand = result.scalar()
    if cross_brand != 0:
        raise RuntimeError(
            f"S1-2 cross-brand consistency check failed: {cross_brand} rows "
            f"have location_id pointing to a location of a different restaurant. "
            f"Manual inspection required before proceeding."
        )


def downgrade() -> None:
    # ── Restore old unique constraint ──────────────────────────────────────
    op.create_unique_constraint(
        "uq_table_restaurant_number",
        "restaurant_tables",
        ["restaurant_id", "table_number"],
    )

    # ── Remove new unique constraint ───────────────────────────────────────
    op.drop_constraint(
        "uq_table_location_number",
        "restaurant_tables",
        type_="unique",
    )

    # ── Remove index ───────────────────────────────────────────────────────
    op.drop_index(
        "ix_restaurant_tables_location_id",
        table_name="restaurant_tables",
    )

    # ── Remove FK constraint ───────────────────────────────────────────────
    op.drop_constraint(
        "fk_restaurant_tables_location_id",
        "restaurant_tables",
        type_="foreignkey",
    )

    # ── Drop location_id column ────────────────────────────────────────────
    op.drop_column("restaurant_tables", "location_id")
