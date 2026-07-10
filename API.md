# Taomly API Reference

Base URL: https://taomly.onrender.com

Authentication: Bearer JWT token in Authorization header.

---

## AUTH — Agency

POST /api/agency/login
Получить токен агентства.

POST /api/agency/register
Зарегистрировать агентство. Rate limit: 5 запросов / 10 минут.

---

## RESTAURANTS

GET /api/agency/restaurants — список ресторанов
POST /api/agency/restaurants — создать ресторан
PATCH /api/agency/restaurants/{id} — обновить
DELETE /api/agency/restaurants/{id} — удалить

---

## AUTH — Restaurant Admin

POST /api/restaurants/login
Войти как администратор ресторана (slug + password).

---

## MENU

GET /api/menu/{slug} — публичное меню (без авторизации)
GET /api/menu/categories — категории
POST /api/menu/categories — создать категорию
POST /api/menu/products — создать блюдо
PATCH /api/menu/products/{id} — обновить блюдо
DELETE /api/menu/products/{id} — удалить блюдо

---

## ORDERS

GET /api/orders/ — список заказов
POST /api/orders/ — создать заказ
PATCH /api/orders/{id}/status — сменить статус

Статусы: new → accepted → preparing → ready_for_delivery → delivering → completed / cancelled

---

## ANALYTICS

GET /api/analytics/summary — KPI сводка
GET /api/analytics/revenue-by-day — выручка по дням
GET /api/analytics/top-dishes — топ блюд
GET /api/analytics/peak-hours — часы пик
GET /api/analytics/order-types — типы заказов

Параметр: ?period=today|7d|30d|90d|this_month

---

## BILLING

GET /api/billing/plans — тарифы
GET /api/billing/subscription — текущая подписка
POST /api/billing/subscribe/{plan_id} — сменить тариф
GET /api/billing/usage — использование
GET /api/billing/invoice/{month} — PDF подтверждение

Планы: Free ($0), Basic ($29), Pro ($79)

---

## AI (активируется при AI_ENABLED=true)

POST /api/ai/generate-description — описание блюда
POST /api/ai/translate-menu — перевод меню
POST /api/ai/suggest-tags — предложить бейджи
POST /api/ai/generate-seo — SEO описание

---

## RESERVATIONS

GET /api/reservations/ — список броней
POST /api/reservations/ — создать бронь
PATCH /api/reservations/{id}/status — сменить статус

---

## WAITER CALLS

GET /api/waiter-calls/ — активные вызовы
POST /api/waiter-calls/ — вызвать официанта
PATCH /api/waiter-calls/{id}/status — сменить статус

---

## WEBHOOKS

POST /webhook/{slug} — Telegram webhook ресторана

---

## Ошибки

400 — ошибка валидации
401 — не авторизован
403 — нет доступа
404 — не найдено
429 — rate limit
500 — ошибка сервера

---

## Архитектура

Три уровня: Agency → Restaurant Admin → End User
Каждый ресторан изолирован по restaurant_id
Telegram токены зашифрованы через Fernet
IDOR защита на всех endpoints
