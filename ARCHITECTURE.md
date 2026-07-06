# ARCHITECTURE.md — Taomly Platform

## Overview

Taomly is a **White Label Multi-Tenant Restaurant SaaS Platform** built on Telegram Mini App. Each restaurant operates as a fully isolated tenant with its own Telegram bot, menu, branding, and admin panel.

---

## System Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    Telegram Clients                          │
│         (customers scanning QR codes at tables)             │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS + Telegram Mini App
┌────────────────────────▼────────────────────────────────────┐
│                  FastAPI Application                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  index.html  │  │  admin.html  │  │ agency_admin.html│  │
│  │  (PWA Mini   │  │  (Restaurant │  │  (Agency Owner   │  │
│  │   App)       │  │   Admin)     │  │   Dashboard)     │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                   API Routers                          │ │
│  │  /api/orders  /api/menu  /api/agency  /api/restaurants │ │
│  │  /api/reservations  /api/waiter-calls                  │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │   Auth   │  │ Security │  │  Config  │  │  Sentry   │  │
│  │  JWT +   │  │  CORS +  │  │ (env)    │  │ (errors)  │  │
│  │  Fernet  │  │   CSP +  │  │          │  │           │  │
│  │  HMAC    │  │ RateLimit│  │          │  │           │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │ SQLAlchemy (sync)
┌────────────────────────▼────────────────────────────────────┐
│                  Neon PostgreSQL                             │
│  agencies → restaurants → categories → products             │
│                        → orders → order_items               │
│                        → reservations                        │
│                        → waiter_calls                        │
│                        → restaurant_tables                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Multi-Tenant Model

### Hierarchy
```
Agency Owner (1)
  └── Restaurant A (slug: chinar, bot: @chinar_bot)
  │     └── Categories → Products
  │     └── Orders, Reservations, Waiter Calls
  └── Restaurant B (slug: palace, bot: @palace_bot)
        └── Categories → Products
        └── Orders, Reservations, Waiter Calls
```

### Isolation Mechanism

| Layer | How it works |
|-------|-------------|
| **JWT** | Every token contains `restaurant_id` and `agency_id`. All DB queries filter by these. |
| **IDOR** | Every mutating endpoint verifies resource ownership against JWT claims before proceeding. |
| **Telegram** | `initData` is verified with HMAC-SHA256 using **the restaurant's own bot token** (not a shared secret). |
| **Bot Tokens** | Encrypted with Fernet before storing in DB. Decrypted only at request time. |
| **Webhooks** | Each restaurant has its own webhook: `POST /webhook/{slug}`. Secret token verified on every request. |

---

## Authentication Flows

### Flow 1: Customer (Telegram Mini App)
```
1. Customer scans QR code → opens Telegram Mini App
2. Mini App sends requests with headers:
   - X-Telegram-Init-Data: <Telegram initData>
   - X-Restaurant-Id: <restaurant.id>
3. FastAPI → get_telegram_user():
   a. Load restaurant by X-Restaurant-Id
   b. Decrypt bot token (Fernet)
   c. Verify initData HMAC-SHA256 with bot token
   d. Check auth_date age (< MAX_INIT_DATA_AGE_SECONDS)
   e. Return TelegramUser(id, name, restaurant)
4. Router uses tg_user.restaurant for all DB queries
```

### Flow 2: Restaurant Admin
```
1. POST /api/agency/restaurant-login {slug, password}
2. FastAPI verifies bcrypt(password) against admin_password_hash
3. Returns JWT {role: "restaurant_admin", restaurant_id, agency_id}
4. Admin panel uses Bearer token for all API calls
```

### Flow 3: Agency Owner
```
1. POST /api/agency/login {email, password}
2. FastAPI verifies bcrypt(password) against owner_password_hash
3. Returns JWT {role: "agency_owner", agency_id}
4. Agency dashboard can only see restaurants WHERE agency_id = token.agency_id
```

---

## Database Schema

```sql
agencies
  id, name, owner_email, owner_password_hash, is_active, created_at, updated_at

restaurants
  id, agency_id → agencies.id (RESTRICT)
  name, slug (UNIQUE), description, phone, address
  is_active, is_waiter_call_enabled
  admin_password_hash
  logo_url, primary_color, secondary_color, accent_color, welcome_text
  custom_domain (UNIQUE)
  telegram_bot_token_encrypted, telegram_dispatcher_id
  created_at, updated_at

categories
  id, restaurant_id → restaurants.id (CASCADE)
  name (UNIQUE per restaurant), sort_order

products
  id, restaurant_id → restaurants.id (CASCADE)
  category_id → categories.id (SET NULL)
  name, description, price (integer sums UZS)
  photo_url, is_available, sort_order
  is_bestseller, is_new, is_spicy, is_chef_choice  ← badges
  updated_at

restaurant_tables
  id, restaurant_id → restaurants.id (CASCADE)
  table_number (UNIQUE per restaurant)

orders
  id, restaurant_id → restaurants.id (RESTRICT)
  client_id → users.id (SET NULL), client_telegram_id
  client_name, client_phone
  order_type: delivery | takeaway | dine_in
  address, location_lat, location_lng
  table_id → restaurant_tables.id (SET NULL)
  comment, total_amount, status
  created_at, updated_at

order_items
  id, order_id → orders.id (CASCADE)
  product_id → products.id (SET NULL)
  name (snapshot), price (snapshot), quantity

reservations
  id, restaurant_id → restaurants.id (CASCADE)
  client_name, client_phone, guests_count
  reservation_time, comment, status
  created_at, updated_at

waiter_calls
  id, restaurant_id → restaurants.id (CASCADE)
  table_id → restaurant_tables.id (CASCADE)
  status, created_at, updated_at
```

### Key Design Decisions

- **Price snapshot in OrderItem**: name and price copied at order time — menu changes don't affect historical orders.
- **RESTRICT on orders→restaurants**: can't delete restaurant with existing orders (use soft delete via `is_active`).
- **BigInteger PKs**: future-proof for large scale (vs Integer).
- **Timezone-aware timestamps**: all timestamps use `TIMESTAMP(timezone=True)`.

---

## Security Architecture

| Threat | Mitigation |
|--------|-----------|
| XSS | CSP headers, JWT only in memory (not localStorage/sessionStorage) |
| CSRF | CORS configured with explicit allowed origins |
| Clickjacking | `X-Frame-Options: DENY` |
| Brute force | slowapi rate limiting: 10/min on login endpoints |
| IDOR | Every endpoint verifies resource ownership against JWT |
| Replay attack (Telegram) | `auth_date` max age enforced (configurable) |
| Bot token theft | Fernet encryption at rest, never logged |
| Webhook spoofing | HMAC secret token verification on every webhook request |
| Dependency vulnerabilities | Pinned versions in requirements.txt |

---

## Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Framework | FastAPI | Async support, automatic OpenAPI, Python type hints |
| ORM | SQLAlchemy (sync) | Stability; async migration planned for Stage 2 |
| Database | Neon PostgreSQL | Serverless, scales to zero, free tier |
| Hosting | Render | Simple deploys, free SSL, webhooks support |
| Bot framework | pytelegrambotapi | Stable, battle-tested, webhook support |
| Encryption | Fernet (cryptography) | Symmetric, authenticated, standard |
| Auth | python-jose JWT + passlib bcrypt | Industry standard |
| Monitoring | Sentry | Error tracking, performance monitoring |

---

## Known Technical Debt

| ID | Description | Planned for |
|----|-------------|-------------|
| TD-1 | Sync SQLAlchemy blocks event loop | Stage 2 |
| TD-2 | `_BOT_CACHE` in-process dict breaks with 2+ workers | Before scaling |
| TD-3 | Webhook accepts raw `dict` — no Pydantic TelegramUpdate schema | Stage 2 |
| TD-4 | `manifest.json` is static — no per-restaurant branding in PWA | Stage 2 |
| TD-5 | Telegram notifications hardcoded in Uzbek — no i18n | Stage 2 |
| TD-6 | No pagination on order history endpoint | Stage 2 |
| TD-7 | No Redis — rate limiting state lost on restart | Before scaling |
