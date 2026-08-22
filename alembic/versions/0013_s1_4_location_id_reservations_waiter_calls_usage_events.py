"""add location_id to reservations, waiter_calls, usage_events

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-22

Stage 1 — S1-4: location_id на reservations, waiter_calls, usage_events.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ТАБЛИЦА 1: reservations
  FK:       reservations.location_id → locations.id ON DELETE RESTRICT
  Nullable: NOT NULL (после backfill)
  Backfill: reservations.restaurant_id → locations.restaurant_id → locations.id
            MIN(l.id) — детерминированный выбор
  Проверки: orphan rows = 0, cross-brand consistency = 0

ТАБЛИЦА 2: waiter_calls
  FK:       waiter_calls.location_id → locations.id ON DELETE CASCADE
  Nullable: NOT NULL (после backfill)
  Backfill: waiter_calls.restaurant_id → locations.restaurant_id → locations.id
            MIN(l.id) — детерминированный выбор
  Проверки: orphan rows = 0, cross-brand consistency = 0

ТАБЛИЦА 3: usage_events
  FK:       usage_events.location_id → locations.id ON DELETE SET NULL
  Nullable: nullable (исторические events не ломаются)
  Backfill: restaurant_id → locations.restaurant_id → locations.id
            только там где существует однозначная Location
            (0 или >1 Location → location_id остаётся NULL)
  Проверки: orphan rows = 0 (SET NULL защищает от dangling FK)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ЧТО НЕ ДЕЛАЕТСЯ:
  - НЕ удаляется restaurant_id (legacy, до migration 0015)
  - НЕ изменяется billing/menu/user архитектура
  - НЕ исправляются pre-existing failures
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Порядок операций (каждая таблица):
  1. ADD COLUMN location_id BIGINT NULL
  2. BACKFILL via UPDATE JOIN locations
  3. VERIFY orphan rows = 0
  4. VERIFY cross-brand consistency (только NOT NULL таблицы)
  5. ALTER COLUMN SET NOT NULL (reservations, waiter_calls) / пропустить (usage_events)
  6. ADD FK с нужным ON DELETE
  7. ADD INDEX ix_<table>_location_id

Downgrade:
  DROP INDEX → DROP FK → DROP COLUMN (в обратном порядке таблиц)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# BACKFILL SQL — общий шаблон: restaurant_id → MIN(location.id)
# migration 0010 гарантирует 1 Location per Restaurant при backfill.
# MIN(l.id) — детерминированный выбор при гипотетическом >1.
# ─────────────────────────────────────────────────────────────────────────────
_BACKFILL_RESERVATIONS = sa.text("""
    UPDATE reservations r
    SET location_id = (
        SELECT l.id
        FROM locations l
        WHERE l.restaurant_id = r.restaurant_id
        ORDER BY l.id
        LIMIT 1
    )
    WHERE r.location_id IS NULL
""")

_BACKFILL_WAITER_CALLS = sa.text("""
    UPDATE waiter_calls wc
    SET location_id = (
        SELECT l.id
        FROM locations l
        WHERE l.restaurant_id = wc.restaurant_id
        ORDER BY l.id
        LIMIT 1
    )
    WHERE wc.location_id IS NULL
""")

# usage_events: backfill только там, где ровно одна Location для restaurant_id.
# Если 0 или >1 — оставляем NULL (nullable, SET NULL on delete).
_BACKFILL_USAGE_EVENTS = sa.text("""
    UPDATE usage_events ue
    SET location_id = (
        SELECT l.id
        FROM locations l
        WHERE l.restaurant_id = ue.restaurant_id
        ORDER BY l.id
        LIMIT 1
    )
    WHERE ue.location_id IS NULL
      AND (
          SELECT COUNT(*)
          FROM locations l
          WHERE l.restaurant_id = ue.restaurant_id
      ) = 1
""")


def _verify_orphans(bind, table: str) -> None:
    """Проверяет orphan rows (location_id IS NULL после backfill) для NOT NULL таблиц."""
    result = bind.execute(sa.text(
        f"SELECT COUNT(*) FROM {table} WHERE location_id IS NULL"
    ))
    null_count = result.scalar()
    if null_count != 0:
        raise RuntimeError(
            f"S1-4 backfill failed: {null_count} rows in '{table}' have "
            f"location_id IS NULL after backfill. "
            f"Ensure migration 0010 ran first and every restaurant_id in "
            f"'{table}' has a matching entry in locations."
        )


def _verify_cross_brand(bind, table: str) -> None:
    """Проверяет cross-brand consistency: row.restaurant_id == location.restaurant_id."""
    result = bind.execute(sa.text(f"""
        SELECT COUNT(*)
        FROM {table} t
        JOIN locations l ON l.id = t.location_id
        WHERE t.restaurant_id != l.restaurant_id
    """))
    cross = result.scalar()
    if cross != 0:
        raise RuntimeError(
            f"S1-4 cross-brand consistency check failed for '{table}': "
            f"{cross} rows have location_id pointing to a location of a "
            f"different restaurant. Manual inspection required. "
            f"Query: SELECT t.id, t.restaurant_id, l.restaurant_id AS loc_restaurant "
            f"FROM {table} t JOIN locations l ON l.id = t.location_id "
            f"WHERE t.restaurant_id != l.restaurant_id LIMIT 20;"
        )


def upgrade() -> None:
    bind = op.get_bind()

    # ══════════════════════════════════════════════════════════════════════
    # 1. RESERVATIONS
    # ══════════════════════════════════════════════════════════════════════

    # 1-1. Add column (nullable for backfill)
    op.add_column(
        "reservations",
        sa.Column("location_id", sa.BigInteger(), nullable=True),
    )

    # 1-2. Backfill
    bind.execute(_BACKFILL_RESERVATIONS)

    # 1-3. Verify orphans = 0
    _verify_orphans(bind, "reservations")

    # 1-4. Verify cross-brand
    _verify_cross_brand(bind, "reservations")

    # 1-5. Set NOT NULL
    op.alter_column("reservations", "location_id", nullable=False)

    # 1-6. FK: ON DELETE RESTRICT
    #   RESTRICT: бронь — исторический документ. Удаление Location с бронями запрещено.
    op.create_foreign_key(
        "fk_reservations_location_id",
        "reservations",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 1-7. Index
    op.create_index("ix_reservations_location_id", "reservations", ["location_id"])

    # ══════════════════════════════════════════════════════════════════════
    # 2. WAITER_CALLS
    # ══════════════════════════════════════════════════════════════════════

    # 2-1. Add column (nullable for backfill)
    op.add_column(
        "waiter_calls",
        sa.Column("location_id", sa.BigInteger(), nullable=True),
    )

    # 2-2. Backfill
    bind.execute(_BACKFILL_WAITER_CALLS)

    # 2-3. Verify orphans = 0
    _verify_orphans(bind, "waiter_calls")

    # 2-4. Verify cross-brand
    _verify_cross_brand(bind, "waiter_calls")

    # 2-5. Set NOT NULL
    op.alter_column("waiter_calls", "location_id", nullable=False)

    # 2-6. FK: ON DELETE CASCADE
    #   CASCADE: вызов официанта — оперативная запись, не исторический документ.
    #   Удаление Location удаляет и вызовы (нет смысла хранить вызовы закрытой точки).
    op.create_foreign_key(
        "fk_waiter_calls_location_id",
        "waiter_calls",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 2-7. Index
    op.create_index("ix_waiter_calls_location_id", "waiter_calls", ["location_id"])

    # ══════════════════════════════════════════════════════════════════════
    # 3. USAGE_EVENTS
    # ══════════════════════════════════════════════════════════════════════

    # 3-1. Add column (nullable — остаётся nullable)
    op.add_column(
        "usage_events",
        sa.Column("location_id", sa.BigInteger(), nullable=True),
    )

    # 3-2. Backfill (только для restaurant с ровно 1 Location)
    bind.execute(_BACKFILL_USAGE_EVENTS)

    # 3-3. Verify: no FK-orphan rows (location_id указывает на несуществующую Location)
    #   Проверяем только заполненные (не NULL) location_id.
    result = bind.execute(sa.text("""
        SELECT COUNT(*)
        FROM usage_events ue
        WHERE ue.location_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM locations l WHERE l.id = ue.location_id
          )
    """))
    orphan_fk = result.scalar()
    if orphan_fk != 0:
        raise RuntimeError(
            f"S1-4: {orphan_fk} usage_events rows have location_id pointing "
            f"to non-existent locations. Data integrity error."
        )

    # 3-4. FK: ON DELETE SET NULL
    #   SET NULL: исторические billing events не удаляются при закрытии Location.
    #   location_id становится NULL — event сохраняется для аудита.
    op.create_foreign_key(
        "fk_usage_events_location_id",
        "usage_events",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3-5. Index (partial WHERE NOT NULL помогает для аналитики)
    op.create_index("ix_usage_events_location_id", "usage_events", ["location_id"])


def downgrade() -> None:
    # usage_events
    op.drop_index("ix_usage_events_location_id", table_name="usage_events")
    op.drop_constraint("fk_usage_events_location_id", "usage_events", type_="foreignkey")
    op.drop_column("usage_events", "location_id")

    # waiter_calls
    op.drop_index("ix_waiter_calls_location_id", table_name="waiter_calls")
    op.drop_constraint("fk_waiter_calls_location_id", "waiter_calls", type_="foreignkey")
    op.drop_column("waiter_calls", "location_id")

    # reservations
    op.drop_index("ix_reservations_location_id", table_name="reservations")
    op.drop_constraint("fk_reservations_location_id", "reservations", type_="foreignkey")
    op.drop_column("reservations", "location_id")
