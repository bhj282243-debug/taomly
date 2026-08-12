"""add restaurant language field

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-12

Изменения:
  - restaurants.language (VARCHAR 5, nullable=False, default 'uz')

    Язык клиентского интерфейса ресторана — определяет язык Telegram-уведомлений
    для клиентов и язык UI в Mini App / PWA.

    НЕ путать с SubscriptionPlan.currency или Restaurant.currency —
    это поле не влияет на форматирование цен.

    Поддерживаемые значения (CHECK constraint):
      uz — Uzbek  (основной рынок, Узбекистан)
      ru — Russian
      en — English

    Дефолт 'uz' — основной рынок. Существующие рестораны получат
    'uz' автоматически — поведение клиентских уведомлений не меняется.

Обратная миграция: DROP COLUMN language (потеря данных если были изменены).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "restaurants",
        sa.Column(
            "language",
            sa.String(5),
            nullable=False,
            server_default="uz",
        ),
    )
    # CHECK constraint: только разрешённые коды языков.
    # Защищает от случайных опечаток при прямом редактировании БД.
    op.create_check_constraint(
        "ck_restaurants_language",
        "restaurants",
        "language IN ('uz', 'ru', 'en')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_restaurants_language", "restaurants", type_="check")
    op.drop_column("restaurants", "language")
