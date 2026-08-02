"""add delivery fields to restaurants

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26

Добавляет три колонки в таблицу restaurants:

  working_hours     — строка «09:00-23:00» или NULL (значит «всегда открыто»).
                      Формат свободный — ресторан сам пишет что хочет показать клиенту.
                      Например: «Пн-Пт 10:00-22:00, Сб-Вс 11:00-23:00»

  delivery_fee      — стоимость доставки в сомах (целое, 0 = бесплатно).
                      Показывается клиенту в корзине при выборе типа «Доставка».

  min_order_amount  — минимальная сумма заказа в сомах (0 = без ограничения).
                      При оформлении заказа backend проверяет total >= min_order_amount.

Для существующих БД без Alembic:
  ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS working_hours     VARCHAR(50)  DEFAULT NULL;
  ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS delivery_fee      INTEGER      NOT NULL DEFAULT 0;
  ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS min_order_amount  INTEGER      NOT NULL DEFAULT 0;
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем delivery-колонки идемпотентно (IF NOT EXISTS)
    op.execute("ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS working_hours    VARCHAR(50)  DEFAULT NULL")
    op.execute("ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS delivery_fee     INTEGER      NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS min_order_amount INTEGER      NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.drop_column("restaurants", "min_order_amount")
    op.drop_column("restaurants", "delivery_fee")
    op.drop_column("restaurants", "working_hours")
