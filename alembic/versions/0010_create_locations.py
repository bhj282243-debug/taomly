"""create locations table and backfill from restaurants

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-21

Stage 1 — S1-1: Location Entity.

Создаёт таблицу locations и выполняет backfill:
  1 существующий Restaurant Brand = 1 начальная Location.

Схема соответствует S1-1 Specification (LOCKED).

Backfill-правила:
  location.slug = restaurant.slug       (backfill slug = restaurant slug →
                                         webhook /webhook/{slug} продолжает
                                         работать без перерегистрации)
  location.name = restaurant.name
  location.is_active = restaurant.is_active
  Все операционные поля (address, phone, timezone, ...) копируются из Restaurant.
  Restaurant поля НЕ удаляются — backward compat до Migration 0015.

ON DELETE RESTRICT для restaurant_id:
  Нельзя удалить Brand пока существуют Location.
  Brand деактивируется soft (is_active=False), не физически удаляется.

Для применения на существующей production БД:
  alembic upgrade 0010

Для отката:
  alembic downgrade 0009
  (удаляет locations таблицу; данные теряются — только в dev/staging)

Idempotency:
  Если таблица locations уже существует (например, создана вручную),
  миграция завершится с ошибкой. В этом случае:
    alembic stamp 0010
  чтобы пометить как применённую без повторного выполнения.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Create locations table ─────────────────────────────────────────
    op.create_table(
        "locations",

        sa.Column("id", sa.BigInteger(), nullable=False),

        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),

        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),

        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),

        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),

        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default="Asia/Tashkent",
        ),
        sa.Column("working_hours", sa.String(100), nullable=True),

        sa.Column(
            "delivery_fee",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "min_order_amount",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),

        sa.Column(
            "currency",
            sa.String(10),
            nullable=False,
            server_default="UZS",
        ),
        sa.Column(
            "language",
            sa.String(5),
            nullable=False,
            server_default="uz",
        ),

        sa.Column(
            "is_waiter_call_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),

        sa.Column("telegram_bot_token_encrypted", sa.Text(), nullable=True),
        sa.Column("telegram_dispatcher_id", sa.BigInteger(), nullable=True),

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

        # Constraints
        sa.ForeignKeyConstraint(
            ["restaurant_id"],
            ["restaurants.id"],
            ondelete="RESTRICT",
            name="fk_locations_restaurant_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_locations_slug"),
        sa.CheckConstraint(
            "delivery_fee >= 0",
            name="ck_locations_delivery_fee_nonnegative",
        ),
        sa.CheckConstraint(
            "min_order_amount >= 0",
            name="ck_locations_min_order_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "currency IN ('UZS', 'KZT', 'RUB', 'USD', 'TRY', 'AED')",
            name="ck_locations_currency",
        ),
        sa.CheckConstraint(
            "language IN ('uz', 'ru', 'en')",
            name="ck_locations_language",
        ),
    )

    # ── 2. Indexes ────────────────────────────────────────────────────────
    # ix_locations_restaurant_active: hot path для получения всех Location бренда
    op.create_index(
        "ix_locations_restaurant_active",
        "locations",
        ["restaurant_id", "is_active"],
    )

    # ── 3. Backfill: 1 Location per existing Restaurant ───────────────────
    #
    # INSERT ... SELECT — атомарная операция в рамках транзакции миграции.
    # Если таблица restaurants пустая — INSERT 0 rows, без ошибок.
    #
    # COALESCE гарантирует NOT NULL defaults для полей, которые могут быть
    # NULL в старых данных (до миграций 0004-0008).
    #
    # slug: location.slug = restaurant.slug.
    # Это обеспечивает backward compat:
    #   - /webhook/{restaurant_slug} → /webhook/{location_slug} = тот же URL
    #   - QR-коды и Mini App links не ломаются
    #   - Telegram webhooks не нужно перерегистрировать
    #
    # UNIQUE(slug) enforcement: поскольку restaurants.slug уже UNIQUE,
    # backfill не может создать дубликаты.

    op.execute(sa.text("""
        INSERT INTO locations (
            restaurant_id,
            name,
            slug,
            is_active,
            address,
            phone,
            timezone,
            working_hours,
            delivery_fee,
            min_order_amount,
            currency,
            language,
            is_waiter_call_enabled,
            telegram_bot_token_encrypted,
            telegram_dispatcher_id,
            created_at,
            updated_at
        )
        SELECT
            id                                              AS restaurant_id,
            name                                            AS name,
            slug                                            AS slug,
            is_active                                       AS is_active,
            address                                         AS address,
            phone                                           AS phone,
            COALESCE(timezone, 'Asia/Tashkent')             AS timezone,
            working_hours                                   AS working_hours,
            COALESCE(delivery_fee, 0)                       AS delivery_fee,
            COALESCE(min_order_amount, 0)                   AS min_order_amount,
            COALESCE(currency, 'UZS')                       AS currency,
            COALESCE(language, 'uz')                        AS language,
            COALESCE(is_waiter_call_enabled, false)         AS is_waiter_call_enabled,
            telegram_bot_token_encrypted                    AS telegram_bot_token_encrypted,
            telegram_dispatcher_id                          AS telegram_dispatcher_id,
            created_at                                      AS created_at,
            updated_at                                      AS updated_at
        FROM restaurants
        ORDER BY id
    """))


def downgrade() -> None:
    # Удаляет таблицу locations.
    # Данные теряются — выполнять только в dev/staging.
    # Production: не использовать downgrade без явного approval.
    op.drop_index("ix_locations_restaurant_active", table_name="locations")
    op.drop_table("locations")
