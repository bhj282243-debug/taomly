-- MIGRATION_badges.sql
-- Taomly Stage 1 — добавление badge-колонок в products
-- Выполнить в Neon SQL Editor (однократно)
--
-- Зачем: бейджи блюд (#bestseller, #new, #spicy, #chef_choice)
-- ранее кодировались в поле description через хэштеги.
-- Теперь отдельные булевые колонки — быстрее, чище, готово к AI-аналитике.
--
-- Безопасность: ADD COLUMN IF NOT EXISTS — миграция идемпотентна,
-- можно запускать повторно без риска.

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS is_bestseller  BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_new         BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_spicy       BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_chef_choice BOOLEAN NOT NULL DEFAULT FALSE;

-- Индекс для AI-аналитики Этапа 2: быстрый поиск всех хитов продаж
-- (SELECT * FROM products WHERE is_bestseller = TRUE AND restaurant_id = ?)
CREATE INDEX IF NOT EXISTS ix_products_bestseller
    ON products (restaurant_id, is_bestseller)
    WHERE is_bestseller = TRUE;

-- Проверка
SELECT
    column_name,
    data_type,
    column_default,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'products'
  AND column_name IN ('is_bestseller', 'is_new', 'is_spicy', 'is_chef_choice')
ORDER BY column_name;
