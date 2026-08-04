-- ─────────────────────────────────────────────────────────────────────────────
-- MIGRATION_badges.sql
-- Добавляет булевые badge-колонки в таблицу products.
-- Колонки: is_bestseller, is_new, is_spicy, is_chef_choice
--
-- История: эта миграция была применена вручную до введения Alembic.
--          Alembic-эквивалент: 0002_add_badge_columns.py
--
-- Идемпотентность: все операторы используют IF NOT EXISTS — безопасны
--                  при повторном запуске.
--
-- Применять: только если 0002_add_badge_columns.py ещё не применялась.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS is_bestseller  BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS is_new         BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS is_spicy       BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS is_chef_choice BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index для быстрого поиска хитов продаж
CREATE INDEX IF NOT EXISTS ix_products_bestseller
    ON products (restaurant_id, is_bestseller)
    WHERE is_bestseller = TRUE;

-- ─────────────────────────────────────────────────────────────────────────────
-- Проверка:
-- SELECT column_name FROM information_schema.columns
-- WHERE table_name = 'products'
--   AND column_name IN ('is_bestseller','is_new','is_spicy','is_chef_choice');
-- ─────────────────────────────────────────────────────────────────────────────
