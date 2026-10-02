"""phase15_reservation_phone_digits_topup

Phase 15 (MC-07), M2: добор client_phone_digits после деплоя Phase 15.

Между миграцией 0030 и деплоем нового кода старый код мог создать брони без
client_phone_digits (колонка у них NULL). Этот шаг заполняет такие строки.
Строки с уже заполненным значением не затрагиваются.

Порядок: 0030 (M1) → деплой Phase 15 → 0031 (M2).
Схема не меняется; downgrade ничего не откатывает (данные цифр безвредны).

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-01 00:00:01.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOPUP_SQL = (
    "UPDATE reservations "
    "SET client_phone_digits = regexp_replace(client_phone, '[^0-9]', '', 'g') "
    "WHERE client_phone_digits IS NULL"
)


def upgrade() -> None:
    op.execute(sa.text(TOPUP_SQL))


def downgrade() -> None:
    # Данные-only шаг: схема не менялась, откатывать нечего.
    pass
