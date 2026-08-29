"""S2-8 — Order Item Modifiers

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-29

S2-8: Добавляет таблицу order_item_modifiers для хранения snapshot
выбранных модификаторов в позиции заказа.

Новые таблицы:
  order_item_modifiers — snapshot выбранных ModifierOption на момент заказа.

Архитектурные решения:
  ADR-S2-8-1: modifier_option_id SET NULL (не CASCADE).
               Удаление ModifierOption сохраняет историю заказа —
               name и price_adjustment остаются в snapshot-полях.
  ADR-S2-8-2: name и price_adjustment — обязательный snapshot из БД.
               Клиентские значения не принимаются никогда.
  ADR-S2-8-3: OrderItem.price НЕ включает modifier price_adjustment.
               Пересчёт цены через модификаторы — Phase 7.
  ADR-S2-8-4: Tenant-цепочка:
               order_item_modifiers → order_item_id → order_items → order_id →
               orders → restaurant_id.
               Валидация на уровне API (не constraint) — модификатор
               обязан принадлежать тому же продукту, что и OrderItem.

Цепочка: 0014 → 0015 → 0016
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0016"
down_revision: str = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── order_item_modifiers ─────────────────────────────────────────────
    # Snapshot выбранных модификаторов. Один OrderItem → N записей (по одной
    # на каждую выбранную ModifierOption).
    #
    # Поля:
    #   order_item_id:       FK → order_items.id CASCADE DELETE.
    #                        При удалении OrderItem модификаторы тоже удаляются.
    #   modifier_option_id:  FK → modifier_options.id SET NULL.
    #                        При удалении ModifierOption snapshot сохраняется
    #                        (name + price_adjustment остаются).
    #   name:                NOT NULL snapshot имени опции на момент заказа.
    #   price_adjustment:    NOT NULL snapshot price_adjustment на момент заказа.
    #                        Знаковое целое. Не влияет на OrderItem.price (Phase 7).
    op.create_table(
        "order_item_modifiers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_item_id", sa.BigInteger(), nullable=False),
        sa.Column("modifier_option_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("price_adjustment", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name="fk_order_item_modifiers_order_item_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["modifier_option_id"],
            ["modifier_options.id"],
            name="fk_order_item_modifiers_modifier_option_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Индекс для поиска всех модификаторов позиции заказа.
    op.create_index(
        "ix_order_item_modifiers_order_item_id",
        "order_item_modifiers",
        ["order_item_id"],
    )
    # Индекс для аналитики по конкретной опции (сколько раз выбирали).
    op.create_index(
        "ix_order_item_modifiers_option_id",
        "order_item_modifiers",
        ["modifier_option_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_order_item_modifiers_option_id", table_name="order_item_modifiers")
    op.drop_index("ix_order_item_modifiers_order_item_id", table_name="order_item_modifiers")
    op.drop_table("order_item_modifiers")
