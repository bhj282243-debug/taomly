# PHASE 14 — GATE RESULT — Taomly Platform

**Date:** 2026-09-30
**Signed off by:** Owner Decision
**Version:** — (версия не присваивалась; `CHANGELOG.md` не изменялся)

---

## STATUS

**STATUS: CLOSED**
**GATE: PASS WITH BASELINE EXCEPTION**

> **Это не CI GREEN.** Phase 14 закрыта по Owner Decision с baseline exception,
> поскольку существующие красные CI jobs содержат pre-existing проблемы,
> подтверждённые baseline, а новых Phase 14 regression не обнаружено.

---

## 1. SCOPE

| Область | Содержание |
|---------|------------|
| Delivery | CRUD зон доставки (`delivery_zones`), публичный список зон, серверный расчёт `delivery_fee` / `subtotal` / `total_amount`, минимальный заказ, fallback на `Location` |
| DINE_IN | Использование существующего endpoint `GET /{slug}/table/{table_number}` (rate limit 10/min, фильтр `is_active`) |
| Pickup | Время приготовления `locations.preparation_time_minutes` |
| Scheduled orders | `orders.scheduled_at`, границы, статус `new`, activation loop, уведомления |
| Waiter Call | Флаг `is_waiter_call_enabled` (SEC-05), локация как источник истины |
| Idempotency | `carts.order_id` (SEC-03), `FOR UPDATE NOWAIT` |
| Migrations | 0025–0029 |

---

## 2. IMPLEMENTATION RESULT

Phase 14 функционально завершена.

- Стоимость доставки, `subtotal` и `total_amount` считаются на сервере; `Payment.amount` берётся из `order.total_amount`.
- Чужие / неактивные `zone_id`, `table_id`, `location_id` отклоняются (tenant isolation).
- Отложенный заказ создаётся со статусом `new`, мгновенный остаётся `accepted`. При создании отложенного заказа отправляется `notify_client_scheduled`; `notify_client_accepted` отправляется только при активации (`new → accepted`).
- KDS показывает только `accepted`, `preparing`, `ready_for_delivery`.
- Formula активации: `scheduled_at − (preparation_time + ETA зоны для delivery + 5 минут buffer)`.
- Rate limit `/api/cart/checkout`: 10/minute (SEC-02).

**Исправления после первого CI-прогона Phase 14:**

| # | Файл | Изменение |
|---|------|-----------|
| 1 | `modules/cart/service.py` | `_Optional` → `Optional` (падение сбора тестов) |
| 2 | `tests/test_s1_4_location_id.py` | 3 теста: включён `is_waiter_call_enabled`, `notify_waiter_call` замокан (только тесты) |
| 3 | `tests/test_s1_7_settings_location.py` | 1 тест: `mock_order.delivery_fee = 0` (только тесты) |
| 4 | `tests/test_s1_8_telegram_location.py` | 3 теста: `mock_order.delivery_fee = 0` (только тесты) |
| 5 | `api.py` (строка 376) | `asyncio.CancelledError` → `_asyncio.CancelledError` (Ruff F821) |

**Limitation — спецификация ETA / activation buffer.**
Утверждённая спецификация ETA доставки и activation buffer в repository документально
не подтверждена (в `*.md` файлах нет ссылок на OD-01 / BLOCK-01 / SEC-0x). Проверялась
только реализация в коде и тестах; соответствие утверждённой спецификации
не подтверждено.

---

## 3. PHASE 14 TESTS

**62 / 62 PASS** (PostgreSQL, финальный CI-прогон).

| Файл | Тестов |
|------|--------|
| `test_phase14_delivery_fee.py` | 14 |
| `test_phase14_delivery_zones.py` | 10 |
| `test_phase14_scheduled_orders.py` | 11 |
| `test_phase14_notifications.py` | 6 |
| `test_phase14_security.py` | 6 |
| `test_phase14_waiter_call.py` | 6 |
| `test_phase14_web_dine_in.py` | 6 |
| `test_phase14_idempotency.py` | 3 |

---

## 4. POSTGRESQL BASELINE / CURRENT

| | Failed | Passed | Skipped |
|---|--------|--------|---------|
| Baseline (`main`, до Phase 14, коммит `f2160e8`) | 19 | 1257 | 1 |
| Phase 14 до исправления 7 тестов | 26 | 1312 | 1 |
| **Current** (`main`, коммит `054372d`) | **19** | **1319** | **1** |

- 1257 + 62 = 1319: прирост полностью за счёт новых тестов Phase 14.
- Названия 19 падений совпадают с baseline (сравнение по именам тестов, не по количеству).
- Новых PostgreSQL regression: **0**.

**CI-прогоны, использованные для сравнения:**

| Прогон | Что это | PostgreSQL |
|--------|---------|------------|
| 97151113602 | baseline `main` (`f2160e8`) | 19 / 1257 / 1 |
| 98950586436 | PR #1 (merge `6ea9d97`) | 26 / 1312 / 1 |
| 98970204546 | `main` после merge (`525b89a`) | 26 / 1312 / 1 |
| 98981394536 | `main` после исправления 7 тестов (`0559782`) | 19 / 1319 / 1 |
| 99302691285 | `main` после исправления F821 (`054372d`) | 19 / 1319 / 1 |

---

## 5. MIGRATION 0025–0029

**PASS.** `alembic upgrade head` на чистой PostgreSQL 15 (шаг CI `test-postgres`):
**0001 → 0029** без ошибок, одна head-ревизия (0029).

| Миграция | Содержание |
|----------|------------|
| 0025 | `orders.subtotal`, `orders.delivery_fee` (CHECK ≥ 0) |
| 0026 | таблица `delivery_zones` (CHECK, индекс) |
| 0027 | `orders.delivery_zone_id` (FK), `orders.scheduled_at` (индексы) |
| 0028 | `locations.preparation_time_minutes` (CHECK) |
| 0029 | `carts.order_id` (FK, индекс) |

Для 0025–0029 написан `downgrade`; в рамках gate он не запускался. Прямой запрос
к схеме БД не выполнялся: подтверждение — успешное применение всех ревизий и
прохождение тестов.

---

## 6. SECURITY

Требования Phase 14 — **PASS**:

| ID | Требование | Статус |
|----|-----------|--------|
| SEC-02 | `/api/cart/checkout` rate limit 10/minute | ✅ |
| SEC-03 | `cart.order_id` + `FOR UPDATE NOWAIT`, защита от дубля заказа | ✅ |
| SEC-05 | `is_waiter_call_enabled` enforced в production path (403) | ✅ |
| — | Tenant isolation: `zone_id`, `table_id`, `location_id`, `delivery_zone_id` | ✅ |
| — | Поддельные fee / total / zone / `scheduled_at` отклоняются или игнорируются | ✅ |

Зависимости: `requirements.txt` Phase 14 **не менялся**. Dependency vulnerabilities — см. раздел 8.

---

## 7. REGRESSION

| Проверка | Результат |
|----------|-----------|
| Новые PostgreSQL failures | **0** |
| Новые SQLite failures | **0** (6 failed = baseline) |
| Baseline PostgreSQL failures, изменившиеся или исчезнувшие | 0 (все 19 на месте) |
| Phase 10–13 (KDS, order management, web ordering) | без regression |
| 7 тестовых regression, вызванных Phase 14 | исправлены изменением тестов, production-код не менялся |
| Ruff F821 в `api.py:376` | исправлен |

Про 7 исправленных тестов: причины — новый обязательный флаг `is_waiter_call_enabled`
(3 теста) и чтение `delivery_fee` в `notify_new_order` (4 теста, `MagicMock > 0`).

SQLite (не является официальным gate): baseline 6 failed / 313 passed / 14 skipped / 944 errors;
current 6 failed / 342 passed / 14 skipped / 977 errors. Из 62 тестов Phase 14 в SQLite-job
29 PASSED и 33 ERROR (+33 ошибки = разница 977 − 944). Причина этих 33 ошибок по каждому
тесту не разбиралась.

---

## 8. PRE-EXISTING PROBLEMS

Существовали до Phase 14 (подтверждено baseline) и в рамках Phase 14 **не исправлялись**.

**PostgreSQL — 19 failures:**

| Группа | Тестов |
|--------|--------|
| analytics | 2 |
| billing | 9 |
| branding | 3 |
| error handling | 5 |

**SQLite:** 6 failed + 977 errors (baseline: 6 failed + 944 errors). Первый падающий тест
`tests/test_ai.py::test_generate_description_ai_disabled`; ошибка фикстуры
`NOT NULL constraint failed: restaurants.id`. Официальный gate — PostgreSQL.

**Dependency vulnerabilities — 12 (3 пакета), идентично baseline:**

| Пакет | Версия | Уязвимостей |
|-------|--------|-------------|
| starlette | 0.41.3 | 7 |
| pyasn1 | 0.4.8 | 4 |
| ecdsa | 0.19.2 | 1 |

Severity `pip-audit` не выводит; отдельно не проверялась. Зависимости не обновлялись.

**Ruff baseline:** 888 замечаний. Задание Lint красное и на baseline (`ruff check .` падает
на первом шаге, поэтому `ruff format --check .` не выполнялся).

---

## 9. RUFF PHASE 14 DELTA

| | Значение |
|---|----------|
| Baseline | 888 |
| Current | 959 |
| Добавлено | +87 |
| Убрано | −16 |
| **Нетто** | **+71** |

Сравнение по файлу, коду правила и тексту замечания (номера строк не учитывались).

| Область | Style-only | Non-style | Итого |
|---------|-----------|-----------|-------|
| Production | 42 (`UP007` ×39, `UP017` ×2, `UP035` ×1) | 2 | 44 |
| Tests | 34 (`I001` ×17, `UP017` ×15, `UP035` ×2) | 9 | 43 |

**Non-style, production (2):**
- `handlers.py:659` `DTZ005` — `datetime.now()` без `tz` в уведомлении о вызове официанта (падения нет; время в тексте берётся по серверу, а не по поясу локации);
- `routers/delivery_zones.py:141` `E712` — `== True` в запросе SQLAlchemy (функционально корректно, стилевое замечание).

**Non-style, tests (9):**
- `F401` ×8: неиспользуемые импорты в `test_phase14_delivery_fee.py`, `test_phase14_notifications.py`, `test_phase14_scheduled_orders.py` (5), `test_phase14_waiter_call.py`;
- `F841` ×1: `order2` в `test_phase14_scheduled_orders.py:98`.

Замечаний, способных вызвать runtime-ошибку (F821, F811 в Phase 14 коде), нет: единственный F821 исправлен.
Замечания **не исправлялись**.

---

## 10. DOCKER

**Docker для Phase 14 независимо не подтверждён.**

- В `ci.yml` задание `docker` имеет `needs: [test, test-postgres]` без `if: always()`. Пока хотя бы один тестовый job красный, Docker Build пропускается. На baseline он тоже пропускался.
- От Lint и Audit Docker не зависит.
- Отдельный запуск невозможен без изменения `ci.yml` (`workflow_dispatch` отсутствует); по решению Owner `ci.yml` не менялся.
- Косвенно: `Dockerfile`, `requirements.txt`, `docker-compose.yml` в Phase 14 не менялись; `COPY . .` включает новый `modules/scheduled/`. Это снижает риск, но не заменяет реальную сборку.

---

## 11. OWNER DECISION

**Phase 14 — PASS WITH BASELINE EXCEPTION.**

Owner разрешает закрыть Phase 14 несмотря на существующие красные CI jobs, потому что они
содержат pre-existing failures, подтверждённые baseline. Это **не** означает, что CI
объявляется зелёным.

Оснований для решения:
- 62/62 Phase 14 tests PASS;
- PostgreSQL: 0 новых failures, 19 failures совпадают с baseline по названиям;
- SQLite: новых failures нет;
- migrations 0025–0029 PASS;
- Phase 14 security requirements PASS;
- F821 исправлен;
- dependency vulnerabilities существовали до Phase 14.

Письменного правила о допустимости pre-existing failures в repository нет; решение
принято Owner на основе сравнения с baseline.

---

## 12. KNOWN TECHNICAL DEBT / FOLLOW-UP

Не исправлялось в рамках Phase 14. Следующая фаза не начинается автоматически.

| ID | Пункт | Тип |
|----|-------|-----|
| P14-TD-1 | 19 PostgreSQL failures (analytics 2, billing 9, branding 3, error handling 5) | pre-existing |
| P14-TD-2 | SQLite: 6 failed + 977 errors; фикстуры (`restaurants.id`) | pre-existing |
| P14-TD-3 | 12 dependency vulnerabilities: starlette, pyasn1, ecdsa | pre-existing security |
| P14-TD-4 | Ruff baseline 888 | pre-existing |
| P14-TD-5 | Ruff Phase 14 delta: +87 / −16 / +71 net (см. раздел 9) | Phase 14 |
| P14-TD-6 | Docker Build для Phase 14 не подтверждён (`needs` без `always()`; нет `workflow_dispatch`) | CI |
| P14-TD-7 | Соответствие ETA / activation buffer утверждённой спецификации не подтверждено документально | documentation |
| P14-TD-8 | `handlers.py:659`: время в уведомлении о вызове официанта без часового пояса локации (`DTZ005`) | observation |
| P14-TD-9 | Повторный checkout с тем же idempotency key заново отправляет уведомления (дубль заказа не создаётся; поведение как в `main`) | observation |
| P14-TD-10 | Activation loop выполняет синхронные вызовы БД и Telegram внутри async-цикла; при одном воркере (`--workers 1`) двойной активации нет | observation |
| P14-TD-11 | Часть Phase 14 тестов в SQLite-job завершается ERROR (33 из 62); не разбиралось | Phase 14 / SQLite |

---

*Phase 14 closed: 2026-09-30*
*Gate: PASS WITH BASELINE EXCEPTION — CI is NOT green*
*PostgreSQL: 19 failed / 1319 passed / 1 skipped (baseline: 19 / 1257 / 1)*
*Phase 14 tests: 62/62 | New regressions: 0*
