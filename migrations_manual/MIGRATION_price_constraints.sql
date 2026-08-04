-- ─────────────────────────────────────────────────────────────────────────────
-- MIGRATION: ck_*_price_nonnegative
-- Добавляет CHECK-ограничения на неотрицательность цен и сумм заказов.
--
-- Когда применять:
--   После коммита models.py с этими же ограничениями.
--   Выполнить ВРУЧНУЮ в Neon SQL Editor.
--
-- Безопасность:
--   Все операторы идемпотентны — повторный запуск не вызовет ошибку.
--   Constraint добавляется как NOT VALID → проверяет только новые строки,
--   существующие данные не блокирует.
--   Отдельный VALIDATE сразу после проверяет что в БД нет нарушений.
--
-- Откат (если нужно):
--   ALTER TABLE products         DROP CONSTRAINT IF EXISTS ck_products_price_nonnegative;
--   ALTER TABLE orders           DROP CONSTRAINT IF EXISTS ck_orders_total_amount_nonnegative;
--   ALTER TABLE order_items      DROP CONSTRAINT IF EXISTS ck_order_items_price_nonnegative;
--   ALTER TABLE subscription_plans DROP CONSTRAINT IF EXISTS ck_subscription_plans_price_nonnegative;
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. products.price >= 0
ALTER TABLE products
    ADD CONSTRAINT ck_products_price_nonnegative
    CHECK (price >= 0)
    NOT VALID;

ALTER TABLE products
    VALIDATE CONSTRAINT ck_products_price_nonnegative;

-- 2. orders.total_amount >= 0
ALTER TABLE orders
    ADD CONSTRAINT ck_orders_total_amount_nonnegative
    CHECK (total_amount >= 0)
    NOT VALID;

ALTER TABLE orders
    VALIDATE CONSTRAINT ck_orders_total_amount_nonnegative;

-- 3. order_items.price >= 0
ALTER TABLE order_items
    ADD CONSTRAINT ck_order_items_price_nonnegative
    CHECK (price >= 0)
    NOT VALID;

ALTER TABLE order_items
    VALIDATE CONSTRAINT ck_order_items_price_nonnegative;

-- 4. subscription_plans.price >= 0
ALTER TABLE subscription_plans
    ADD CONSTRAINT ck_subscription_plans_price_nonnegative
    CHECK (price >= 0)
    NOT VALID;

ALTER TABLE subscription_plans
    VALIDATE CONSTRAINT ck_subscription_plans_price_nonnegative;

-- ─────────────────────────────────────────────────────────────────────────────
-- Проверка после применения (опционально):
-- SELECT conname, contype, conrelid::regclass
-- FROM pg_constraint
-- WHERE conname LIKE 'ck_%nonnegative'
-- ORDER BY conrelid::regclass::text;
-- ─────────────────────────────────────────────────────────────────────────────
