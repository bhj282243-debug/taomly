"""add restaurant timezone field

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-10

Изменения:
  - restaurants.timezone (VARCHAR 64, nullable, default 'Asia/Tashkent')
    Используется в analytics.get_peak_hours() для корректного расчёта
    пиковых часов в локальном времени ресторана.
    Без этого поля analytics использует getattr(restaurant, 'timezone', None)
    с fallback на 'Asia/Tashkent' — уже работает, но неявно.

Обратная миграция: DROP COLUMN timezone (потеря данных если были изменены).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "restaurants",
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=True,
            server_default="Asia/Tashkent",
        ),
    )


def downgrade() -> None:
    op.drop_column("restaurants", "timezone")
