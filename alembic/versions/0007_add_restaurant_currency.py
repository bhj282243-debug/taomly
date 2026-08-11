"""add restaurant currency field

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-11

Изменения:
  - restaurants.currency (VARCHAR 10, nullable=False, default 'UZS')

    Валюта конкретного ресторана для отображения цен клиентам и в
    Telegram-уведомлениях. НЕ путать с SubscriptionPlan.currency —
    та отвечает за биллинговую валюту тарифного плана.

    Поддерживаемые значения (CHECK constraint):
      UZS — Uzbek Som        (so'm)
      KZT — Kazakhstani Tenge (₸)
      RUB — Russian Ruble    (₽)
      USD — US Dollar        ($)
      TRY — Turkish Lira     (₺)
      AED — UAE Dirham       (AED)

    Дефолт 'UZS' — основной рынок (Узбекистан). Существующие рестораны
    получат UZS автоматически — поведение не меняется.

Обратная миграция: DROP COLUMN currency (потеря данных если были изменены).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "restaurants",
        sa.Column(
            "currency",
            sa.String(10),
            nullable=False,
            server_default="UZS",
        ),
    )
    # CHECK constraint: только разрешённые коды валют.
    # Защищает от случайных опечаток при прямом редактировании БД.
    op.create_check_constraint(
        "ck_restaurants_currency",
        "restaurants",
        "currency IN ('UZS', 'KZT', 'RUB', 'USD', 'TRY', 'AED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_restaurants_currency", "restaurants", type_="check")
    op.drop_column("restaurants", "currency")
