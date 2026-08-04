# migrations_manual/

Ручные SQL-миграции для выполнения в **Neon SQL Editor**.

Используются потому что Render Free план блокирует Shell и Pre-Deploy Commands,
поэтому Alembic не может запускаться автоматически при деплое.

---

## Порядок применения (свежая установка)

| # | Файл | Когда применять |
|---|---|---|
| 1 | `MIGRATION_badges.sql` | Если 0002 Alembic-миграция не применялась |
| 2 | `MIGRATION_billing.sql` | Если таблицы биллинга ещё не созданы |
| 3 | `MIGRATION_price_constraints.sql` | После коммита `models.py` с CHECK-ограничениями |
| 4 | `MIGRATION_popular_partial_index.sql` | Если индекс `ix_products_popular` уже существует как обычный |

---

## Как применять

1. Открой **Neon Console → SQL Editor**
2. Вставь содержимое нужного файла целиком
3. Нажми **Run**
4. Убедись что ошибок нет

Все файлы идемпотентны — повторный запуск безопасен.

---

## Alembic-миграции (для справки)

Полная история схемы БД через Alembic:

| Revision | Файл | Что делает |
|---|---|---|
| 0001 | `alembic/versions/0001_initial.py` | Начальная схема |
| 0002 | `alembic/versions/0002_add_badge_columns.py` | Badge-колонки products |
| 0003 | `alembic/versions/0003_add_is_popular.py` | is_popular + partial index |
| 0004 | `alembic/versions/0004_add_delivery_fields.py` | Поля доставки restaurants |
