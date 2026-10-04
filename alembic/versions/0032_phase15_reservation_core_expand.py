"""phase15_reservation_core_expand

Phase 15, Slice B (Reservation Core), R1 (expand).

Spec: PHASE_15_SLICE_B_RESERVATION_CORE_ARCHITECTURE_GAP_SPEC_v2.md
(§7 Data Model, §10 Migration Strategy).

Что делает миграция:

- reservations: новые nullable-колонки
    table_id            BIGINT      FK -> restaurant_tables.id ON DELETE SET NULL
    client_telegram_id  BIGINT      без FK; только Verified (tg id > 0), Guest = NULL
    idempotency_key     VARCHAR(64)
    confirmed_at / seated_at / completed_at / cancelled_at / no_show_at
                        TIMESTAMPTZ (write-once, ставит service)
- индекс ix_reservations_table_id (под ON DELETE SET NULL)
- partial UNIQUE INDEX uq_reservations_idempotency
    ON reservations (location_id, idempotency_key) WHERE idempotency_key IS NOT NULL
  (OD-1 = Option B; client_phone_digits в scope НЕ входит)
- locations.is_reservation_enabled BOOLEAN NOT NULL DEFAULT TRUE
  (RD-02 = A: существующие и новые Locations = TRUE)
- check_reservation_status заменяется на TRANSITIONAL: допускает legacy 'new'
  и новые статусы requested/seated/no_show

Чего миграция НЕ делает (это R2 / миграция 0033):

- не переводит 'new' -> 'requested' в данных;
- не убирает 'new' из CHECK;
- не меняет server_default колонки status (остаётся 'new' из 0001);
- 0001_initial.py не затрагивается.

Совместимость: все новые колонки nullable, у is_reservation_enabled default TRUE;
старый код новых колонок не читает и не пишет. Legacy 'new' остаётся допустимым.

Downgrade ограничен намеренно (Spec §10.8): откат прерывается, если в новых
колонках есть данные, есть брони в статусах requested/seated/no_show или есть
Location с is_reservation_enabled = FALSE. Иначе данные/настройка были бы
потеряны молча.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# CHECK-ограничение: имя сохраняется (drop + create в одной транзакции миграции).
STATUS_CHECK_NAME = "check_reservation_status"

# Transitional (R1): legacy 'new' + новые статусы. 'new' убирает только 0033 (R2).
TRANSITIONAL_STATUS_CHECK = (
    "status IN ('new','requested','confirmed','seated','completed','cancelled','no_show')"
)

# Прежнее ограничение из 0001_initial.py (для downgrade).
LEGACY_STATUS_CHECK = "status IN ('new','confirmed','completed','cancelled')"

IDEMPOTENCY_INDEX_NAME = "uq_reservations_idempotency"
IDEMPOTENCY_INDEX_WHERE = "idempotency_key IS NOT NULL"

TABLE_FK_NAME = "fk_reservations_table_id"
TABLE_INDEX_NAME = "ix_reservations_table_id"

LIFECYCLE_TIMESTAMP_COLUMNS = (
    "confirmed_at",
    "seated_at",
    "completed_at",
    "cancelled_at",
    "no_show_at",
)

# Spec §10.6: preflight на дубли перед созданием unique index.
# Первый запуск: idempotency_key только что создан и везде NULL -> дублей быть не может;
# запрос делает это доказанным, а не подразумеваемым.
IDEMPOTENCY_DUPLICATES_SQL = (
    "SELECT location_id, idempotency_key, count(*) AS cnt "
    "FROM reservations "
    "WHERE idempotency_key IS NOT NULL "
    "GROUP BY location_id, idempotency_key "
    "HAVING count(*) > 1"
)

# Spec §10.8: guard downgrade 0032.
DOWNGRADE_RESERVATION_DATA_SQL = (
    "SELECT count(*) FROM reservations "
    "WHERE table_id IS NOT NULL "
    "OR client_telegram_id IS NOT NULL "
    "OR idempotency_key IS NOT NULL "
    "OR confirmed_at IS NOT NULL "
    "OR seated_at IS NOT NULL "
    "OR completed_at IS NOT NULL "
    "OR cancelled_at IS NOT NULL "
    "OR no_show_at IS NOT NULL "
    "OR status IN ('requested','seated','no_show')"
)
DOWNGRADE_LOCATION_FLAG_SQL = (
    "SELECT count(*) FROM locations WHERE is_reservation_enabled = false"
)


def upgrade() -> None:
    # 1. reservations: новые nullable-колонки (без перезаписи таблицы).
    op.add_column("reservations", sa.Column("table_id", sa.BigInteger(), nullable=True))
    op.add_column("reservations", sa.Column("client_telegram_id", sa.BigInteger(), nullable=True))
    op.add_column("reservations", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    for column_name in LIFECYCLE_TIMESTAMP_COLUMNS:
        op.add_column(
            "reservations",
            sa.Column(column_name, sa.TIMESTAMP(timezone=True), nullable=True),
        )

    # 2. table_id -> restaurant_tables.id, ON DELETE SET NULL (бронь — исторический документ).
    op.create_foreign_key(
        TABLE_FK_NAME,
        "reservations",
        "restaurant_tables",
        ["table_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(TABLE_INDEX_NAME, "reservations", ["table_id"])

    # 3. locations.is_reservation_enabled DEFAULT TRUE (RD-02 = A).
    #    PostgreSQL >= 11: ADD COLUMN с константным DEFAULT — metadata-only.
    op.add_column(
        "locations",
        sa.Column(
            "is_reservation_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    # 4. Idempotency, OD-1 = Option B: уникальность (location_id, idempotency_key).
    #    Сначала preflight на дубли (Spec §10.6), затем partial unique index.
    duplicate = op.get_bind().execute(sa.text(IDEMPOTENCY_DUPLICATES_SQL)).first()
    if duplicate is not None:
        raise RuntimeError(
            "0032 aborted: duplicate (location_id, idempotency_key) found in reservations; "
            "cannot create unique index uq_reservations_idempotency"
        )
    op.create_index(
        IDEMPOTENCY_INDEX_NAME,
        "reservations",
        ["location_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text(IDEMPOTENCY_INDEX_WHERE),
        sqlite_where=sa.text(IDEMPOTENCY_INDEX_WHERE),
    )

    # 5. Transitional CHECK: допускает legacy 'new' и новые статусы.
    #    Замена в рамках одной транзакции миграции; имя ограничения сохраняется.
    op.drop_constraint(STATUS_CHECK_NAME, "reservations", type_="check")
    op.create_check_constraint(STATUS_CHECK_NAME, "reservations", TRANSITIONAL_STATUS_CHECK)


def downgrade() -> None:
    bind = op.get_bind()

    # Guard (Spec §10.8): не терять данные и настройку молча.
    reservations_with_data = bind.execute(sa.text(DOWNGRADE_RESERVATION_DATA_SQL)).scalar()
    if reservations_with_data:
        raise RuntimeError(
            "0032 downgrade aborted: reservations contain data in columns added by 0032 "
            "or statuses requested/seated/no_show; downgrading would lose data or violate "
            "the legacy status CHECK"
        )
    disabled_locations = bind.execute(sa.text(DOWNGRADE_LOCATION_FLAG_SQL)).scalar()
    if disabled_locations:
        raise RuntimeError(
            "0032 downgrade aborted: locations with is_reservation_enabled = false exist; "
            "downgrading would silently re-enable reservations"
        )

    # Возврат прежнего CHECK (из 0001) до удаления остальных объектов.
    op.drop_constraint(STATUS_CHECK_NAME, "reservations", type_="check")
    op.create_check_constraint(STATUS_CHECK_NAME, "reservations", LEGACY_STATUS_CHECK)

    op.drop_index(IDEMPOTENCY_INDEX_NAME, table_name="reservations")
    op.drop_column("locations", "is_reservation_enabled")

    op.drop_index(TABLE_INDEX_NAME, table_name="reservations")
    op.drop_constraint(TABLE_FK_NAME, "reservations", type_="foreignkey")

    for column_name in reversed(LIFECYCLE_TIMESTAMP_COLUMNS):
        op.drop_column("reservations", column_name)
    op.drop_column("reservations", "idempotency_key")
    op.drop_column("reservations", "client_telegram_id")
    op.drop_column("reservations", "table_id")
