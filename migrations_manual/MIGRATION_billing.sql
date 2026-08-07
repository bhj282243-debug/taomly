-- ─────────────────────────────────────────────────────────────────────────────
-- MIGRATION_billing.sql
-- ⚠️  УСТАРЕВШИЙ ФАЙЛ — ТОЛЬКО ДЛЯ СПРАВКИ
--
-- Начиная с Alembic revision 0005 (alembic/versions/0005_add_missing_tables.py)
-- таблицы биллинга создаются автоматически через:
--
--   alembic upgrade head
--
-- ЭТОТ ФАЙЛ БОЛЬШЕ НЕ НУЖЕН для новых установок.
-- Не запускайте его на базах, где уже выполнена миграция 0005.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- ДЛЯ СУЩЕСТВУЮЩИХ БД, где этот файл уже был применён вручную:
--
-- Если таблицы subscription_plans / subscriptions / usage_events уже
-- существуют в вашей БД, пропустите миграцию 0005 командой:
--
--   python -m alembic stamp 0005
--
-- Это пометит 0005 как выполненную без попытки создать таблицы заново.
-- ─────────────────────────────────────────────────────────────────────────────
-- ОРИГИНАЛЬНОЕ СОДЕРЖИМОЕ (legacy reference):
-- Таблицы: subscription_plans, subscriptions, usage_events
-- Seed:    Free, Basic, Pro планы (минимальный рабочий набор)
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Тарифные планы
CREATE TABLE IF NOT EXISTS subscription_plans (
    id               SERIAL        PRIMARY KEY,
    name             VARCHAR(50)   NOT NULL UNIQUE,
    price            INTEGER       NOT NULL DEFAULT 0,
    currency         VARCHAR(10)   NOT NULL DEFAULT 'USD',
    orders_per_month INTEGER       NOT NULL DEFAULT 100,
    products_limit   INTEGER       NOT NULL DEFAULT 20,
    users_limit      INTEGER       NOT NULL DEFAULT -1,
    description      TEXT,
    is_active        BOOLEAN       NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_subscription_plans_price_nonnegative CHECK (price >= 0)
);

-- 2. Подписки ресторанов
CREATE TABLE IF NOT EXISTS subscriptions (
    id            BIGSERIAL     PRIMARY KEY,
    restaurant_id BIGINT        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    plan_id       INTEGER       NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,
    started_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ,
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS ix_subscriptions_restaurant_active
    ON subscriptions (restaurant_id, is_active);

-- 3. События использования (для квот)
CREATE TABLE IF NOT EXISTS usage_events (
    id            BIGSERIAL     PRIMARY KEY,
    restaurant_id BIGINT        NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    event_type    VARCHAR(50)   NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT check_usage_event_type
        CHECK (event_type IN ('order_created','product_created','product_deleted'))
);

CREATE INDEX IF NOT EXISTS ix_usage_events_restaurant_month
    ON usage_events (restaurant_id, created_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- SEED: стартовые тарифные планы
-- Значения orders_per_month и products_limit — рекомендуемые стартовые.
-- Можно изменить через Neon SQL Editor или Admin Panel после запуска.
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO subscription_plans (name, price, currency, orders_per_month, products_limit, users_limit, description, is_active)
VALUES
    ('Free',  0,   'USD', 100,  20,  -1, 'Бесплатный тариф — до 100 заказов и 20 блюд в месяц',       TRUE),
    ('Basic', 29,  'USD', 500,  100, -1, 'Базовый тариф — до 500 заказов и 100 блюд в месяц',         TRUE),
    ('Pro',   79,  'USD', 2000, 500, -1, 'Профессиональный — до 2000 заказов и 500 блюд в месяц',     TRUE)
ON CONFLICT (name) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- Проверка после применения:
-- SELECT id, name, price, orders_per_month, products_limit FROM subscription_plans ORDER BY price;
-- ─────────────────────────────────────────────────────────────────────────────
