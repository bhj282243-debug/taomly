"""phase15_reservation_phone_digits

Phase 15 (MC-07), M1 (expand): digits-only телефон брони для гостевых лимитов.

- reservations.client_phone_digits VARCHAR(50) NULL
- backfill существующих строк из client_phone (только цифры 0-9)
- индекс (location_id, client_phone_digits, created_at) под подсчёт лимитов
  по паре (телефон + Location) в БД

Совместимость со старым кодом: колонка nullable, старый код её не читает и не пишет.
Строки, созданные старым кодом между этой миграцией и деплоем Phase 15,
добирает миграция 0031 (M2).

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Тот же SQL выполняет 0031 (добор). Явный диапазон [^0-9] совпадает с
# models.operations.phone_digits (ASCII-цифры), а не с locale-зависимым \D.
BACKFILL_SQL = (
    "UPDATE reservations "
    "SET client_phone_digits = regexp_replace(client_phone, '[^0-9]', '', 'g') "
    "WHERE client_phone_digits IS NULL"
)


def upgrade() -> None:
    op.add_column(
        "reservations",
        sa.Column("client_phone_digits", sa.String(length=50), nullable=True),
    )
    op.execute(sa.text(BACKFILL_SQL))
    op.create_index(
        "ix_reservations_location_phone_created",
        "reservations",
        ["location_id", "client_phone_digits", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reservations_location_phone_created", table_name="reservations")
    op.drop_column("reservations", "client_phone_digits")
