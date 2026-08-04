-- ─────────────────────────────────────────────────────────────────────────────
-- MIGRATION: ix_products_popular → partial index
-- Заменяет обычный индекс на partial (WHERE is_popular = TRUE).
--
-- Когда применять:
--   Если миграция 0003 уже была применена на этой БД (индекс существует
--   как обычный полный индекс). Выполнить ВРУЧНУЮ в Neon SQL Editor.
--
-- Почему partial:
--   Индексирует только строки где is_popular = TRUE — их обычно < 10%
--   от общего числа продуктов. Меньше размером, быстрее обновляется,
--   эффективнее для запросов горизонтального скролла "Популярное" в Mini App.
--   По аналогии с ix_products_bestseller (миграция 0002).
--
-- Идемпотентность:
--   DROP INDEX IF EXISTS — безопасен, не упадёт если индекса нет.
--   CREATE INDEX IF NOT EXISTS — безопасен при повторном запуске.
--
-- Откат:
--   DROP INDEX IF EXISTS ix_products_popular;
--   CREATE INDEX IF NOT EXISTS ix_products_popular
--     ON products (restaurant_id, is_popular);
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Удаляем старый полный индекс
DROP INDEX IF EXISTS ix_products_popular;

-- 2. Создаём partial index
CREATE INDEX IF NOT EXISTS ix_products_popular
    ON products (restaurant_id)
    WHERE is_popular = TRUE;

-- ─────────────────────────────────────────────────────────────────────────────
-- Проверка после применения:
-- SELECT indexname, indexdef
-- FROM pg_indexes
-- WHERE tablename = 'products' AND indexname = 'ix_products_popular';
--
-- Ожидаемый результат в indexdef:
--   CREATE INDEX ix_products_popular ON products (restaurant_id) WHERE is_popular
-- ─────────────────────────────────────────────────────────────────────────────
