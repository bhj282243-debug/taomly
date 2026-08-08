# migrations_manual/

Исторические SQL-файлы. **Для новых установок не нужны.**

---

## Новая установка

Все таблицы создаются автоматически одной командой:

```
alembic upgrade head
```

Это создаёт все 14 таблиц включая биллинг и revoked_tokens.
Ручной SQL больше не требуется.

---

## Файлы в этой папке (legacy)

| Файл | Статус | Примечание |
|---|---|---|
| `MIGRATION_billing.sql` | ⚠️ Legacy | Заменён migration 0005. Запускать только если 0005 уже применён через stamp |
| `MIGRATION_badges.sql` | ⚠️ Legacy | Покрыт migration 0002 |
| `MIGRATION_price_constraints.sql` | ⚠️ Legacy | Покрыт migration 0001 |
| `MIGRATION_popular_partial_index.sql` | ⚠️ Legacy | Покрыт migration 0003 |

---

## Существующая БД (если MIGRATION_billing.sql уже применялся вручную)

Если billing-таблицы уже существуют, пометь migration 0005 как выполненную без запуска:

```bash
python -m alembic stamp 0005
```

⚠️ Используй `stamp` только убедившись что все таблицы из 0005 уже существуют в БД.
Подробнее — в `BUYER_GUIDE.md` → раздел "Existing database".

---

## Полная история Alembic-миграций

| Revision | Что делает |
|---|---|
| 0001 | Начальная схема: все основные таблицы |
| 0002 | Badge-колонки в products |
| 0003 | is_popular + partial index |
| 0004 | Поля доставки в restaurants |
| 0005 | revoked_tokens, subscription_plans, subscriptions, usage_events + seed |
