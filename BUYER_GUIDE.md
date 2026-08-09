# Buyer's Guide

Complete guide for taking ownership of, deploying, and operating the platform.

---

## Table of Contents

1. [What You're Getting](#1-what-youre-getting)
2. [Requirements](#2-requirements)
3. [Environment Variables](#3-environment-variables)
4. [Database Setup](#4-database-setup)
5. [Deploy to Render](#5-deploy-to-render)
6. [First Setup: Superadmin → Agency → Restaurant](#6-first-setup)
7. [Telegram Bots](#7-telegram-bots)
8. [Restaurant Tables & QR Codes](#8-restaurant-tables--qr-codes)
9. [Billing & Subscriptions](#9-billing--subscriptions)
10. [Cloudflare R2 Photo Storage](#10-cloudflare-r2-photo-storage)
11. [Clean Install Verification](#11-clean-install-verification)
12. [Known Limitations](#12-known-limitations)
13. [What's Included in the Sale](#13-whats-included-in-the-sale)

---

## 1. What You're Getting

A production-ready multi-tenant white-label restaurant SaaS platform:

- **Multi-tenant** — one platform, multiple agencies, each with multiple restaurants
- **White-label** — each restaurant has its own Telegram bot, branding, and Mini App
- **Full order flow** — Telegram Mini App → order → admin panel → status updates → client notifications
- **Admin panels** — superadmin, agency admin, restaurant admin
- **Billing module** — subscription plans, usage tracking, PDF invoices
- **PWA** — installable customer-facing app

**Architecture:** FastAPI backend · PostgreSQL · Telegram Bot API · Cloudflare R2 · Render

---

## 2. Requirements

| Component | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.12 also supported |
| PostgreSQL | 15+ | Neon free tier recommended |
| Render | Free or Starter | Web Service hosting |
| Telegram | Any | One bot per restaurant + optional platform bot |
| Cloudflare | Free | R2 object storage for dish photos |

**Accounts you will need:**
- [neon.tech](https://neon.tech) — free PostgreSQL
- [render.com](https://render.com) — web hosting
- [Telegram @BotFather](https://t.me/BotFather) — create bots
- [cloudflare.com](https://cloudflare.com) — R2 photo storage

---

## 3. Environment Variables

Full reference. All variables read from `config.py` — see `.env.example` for a ready-to-copy template.

### Required (app won't start without these)

| Variable | Description | How to generate |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Copy from Neon dashboard |
| `SECRET_KEY` | JWT signing key (min 32 chars) | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FERNET_KEY` | Fernet key for encrypting bot tokens | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SUPERADMIN_PASSWORD_HASH` | bcrypt hash of superadmin password | `python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('yourpassword'))"` |
| `SUPERADMIN_EMAIL` | Superadmin login email | Your choice |
| `WEBHOOK_URL` | Base URL of your deployment | `https://your-app.onrender.com` (no path suffix) |
| `ENVIRONMENT` | Runtime mode | Set to `production` on Render |
| `ALLOWED_ORIGINS` | CORS allowed origins (required in production) | `https://your-app.onrender.com` |

> ⚠️ `FERNET_KEY`: if lost, all encrypted Telegram bot tokens in the database become unrecoverable. Back it up securely.

### Superadmin password — official method

Use `SUPERADMIN_PASSWORD_HASH` (bcrypt). The legacy `SUPERADMIN_PASSWORD` (plain text) is supported for convenience but logs a security warning on startup. Never use plain text in production.

### WEBHOOK_URL — critical semantics

`WEBHOOK_URL` is the **base URL of your deployment** — no path suffix:

```
✅ https://your-app.onrender.com
❌ https://your-app.onrender.com/webhook
❌ https://your-app.onrender.com/app
```

The platform builds all paths from this base:
- `+ /webhook` → Telegram webhook for platform bot
- `+ /webhook/{slug}` → Telegram webhook per restaurant
- `+ /app` → Mini App URL in Telegram buttons

If `BOT_TOKEN` is set but `WEBHOOK_URL` is missing or has a path suffix, the app will **refuse to start** with a clear error.

### Optional

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — | Platform-level Telegram bot token |
| `PLATFORM_NAME` | `Restaurant SaaS Platform` | Shown in PDF invoices |
| `PLATFORM_URL` | — | Shown in PDF invoices |
| `PLATFORM_EMAIL` | — | Shown in PDF invoices |
| `R2_ACCOUNT_ID` | — | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | — | R2 API key |
| `R2_SECRET_ACCESS_KEY` | — | R2 API secret |
| `R2_BUCKET_NAME` | `restaurant-photos` | R2 bucket name |
| `R2_PUBLIC_URL` | auto-derived | Custom domain for serving photos |
| `AI_ENABLED` | `false` | Enable AI features |
| `AI_PROVIDER` | `openrouter` | openrouter / openai / anthropic / gemini |
| `AI_API_KEY` | — | API key for AI provider |
| `AI_MODEL` | `mistralai/mistral-7b-instruct` | Model name |
| `SENTRY_DSN` | — | Sentry error monitoring |
| `ACCESS_TOKEN_EXPIRE_HOURS` | `8` | JWT TTL in hours |
| `MAX_INIT_DATA_AGE_SECONDS` | `3600` | Telegram initData max age |
| `WEBHOOK_SECRET` | auto-derived | Telegram webhook signature key |

---

## 4. Database Setup

### Fresh installation

After deploying to Render, migrations run automatically on startup:

```
alembic upgrade head && uvicorn api:app ...
```

This creates all 14 tables in a single sequence:

```
0001_initial          → agencies, restaurants, users, categories,
                        products, restaurant_tables, orders, order_items,
                        reservations, waiter_calls
0002_add_badge_columns
0003_add_is_popular
0004_add_delivery_fields
0005_add_missing_tables → revoked_tokens, subscription_plans (+ seed),
                          subscriptions, usage_events
```

Verify after deploy:
```bash
alembic current
# Expected: 0005 (head)
```

### Existing database (previously ran MIGRATION_billing.sql manually)

If you already ran `MIGRATION_billing.sql` from the `migrations_manual/` folder, tables `subscription_plans`, `subscriptions`, and `usage_events` already exist. Running migration 0005 will fail.

**Safe procedure:**

1. Check which tables exist:
   ```sql
   SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
   ```

2. If `revoked_tokens` is **missing**, create it before stamping:
   ```sql
   CREATE TABLE IF NOT EXISTS revoked_tokens (
       id BIGSERIAL PRIMARY KEY,
       jti VARCHAR(36) NOT NULL,
       token_type VARCHAR(16) NOT NULL DEFAULT 'access',
       expires_at TIMESTAMPTZ NOT NULL,
       revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       CONSTRAINT uq_revoked_tokens_jti UNIQUE (jti)
   );
   CREATE INDEX IF NOT EXISTS ix_revoked_tokens_expires_at
       ON revoked_tokens (expires_at);
   ```

3. After verifying all tables in 0005 exist with correct columns, mark the migration as applied:
   ```bash
   python -m alembic stamp 0005
   ```

   > ⚠️ `alembic stamp` skips the migration entirely. Use it **only after** confirming the tables are already present and correct. If in doubt, check each table against the model in `models.py` before stamping.

---

## 5. Deploy to Render

1. Fork/transfer the repository to your GitHub
2. Render → **New → Web Service** → connect your repo
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `alembic upgrade head && uvicorn api:app --host 0.0.0.0 --port $PORT --workers 1`
4. Add all required environment variables
5. Deploy → wait for green status
6. Verify: `GET https://your-app.onrender.com/health` → `{"status": "healthy", "db": "ok"}`

---

## 6. First Setup

### Step 1 — Superadmin

1. Open `https://your-app.onrender.com/superadmin`
2. Log in with `SUPERADMIN_EMAIL` and your chosen password
3. From the Superadmin Console you can: manage agencies, view all restaurants, freeze/unfreeze, view platform metrics

### Step 2 — Create Agency

An agency is a reseller (a studio or individual) that manages multiple restaurants.

**Via Superadmin Console:**
- **Agencies → + New Agency** → name, email, password

**Via API:**
```bash
curl -X POST https://your-app.onrender.com/api/superadmin/agencies \
  -H "Authorization: Bearer <superadmin_token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Agency", "email": "agency@example.com", "password": "securepass"}'
```

Agency owner logs in at: `https://your-app.onrender.com/agency-admin`

### Step 3 — Create Restaurant

1. Log in at `/agency-admin` as the agency owner
2. **+ New Restaurant**:
   - **Name** — display name
   - **Slug** — URL identifier (e.g. `pizza-house`) — must be unique
   - **Admin password** — for restaurant staff
   - **Bot Token** — from @BotFather (see Section 7)
   - **Dispatcher ID** — your Telegram user ID (receives order notifications)
3. Click **Create**

| Panel | URL |
|---|---|
| Restaurant Admin | `https://your-app.onrender.com/admin?slug=pizza-house` |
| Customer Mini App | `https://your-app.onrender.com/app?slug=pizza-house` |

### Step 4 — Add Menu

1. Log in to Restaurant Admin
2. **Menu** → **+ Add Category** → **+ Add Item**
3. Upload dish photos (requires R2 configured)

### Step 5 — Test Order

Open the Mini App URL → add items to cart → place order → check Restaurant Admin → change status → client receives Telegram notification.

---

## 7. Telegram Bots

### Create a restaurant bot

1. Telegram → `@BotFather` → `/newbot`
2. Choose name and username (must end in `bot`)
3. Copy the token: `1234567890:AAF...`

### Get your Telegram user ID (Dispatcher ID)

1. Telegram → `@userinfobot` → `/start`
2. Copy your numeric ID (e.g. `331294063`)

### Connect to restaurant

Agency Admin → select restaurant → **Settings** → paste Bot Token + Dispatcher ID → Save.

The platform automatically calls `setWebhook` on Telegram's API. Verify:
```bash
curl https://api.telegram.org/bot{TOKEN}/getWebhookInfo
# Expected: "url": "https://your-app.onrender.com/webhook/pizza-house"
```

### Platform bot (optional)

Set `BOT_TOKEN` in Render env → redeploy. This enables a platform-wide bot where users can browse all restaurants via `/start`.

### How it works

- Customer sends `/start` to the restaurant bot → receives Mini App button
- Customer places order → restaurant receives Telegram notification
- Restaurant changes order status → customer receives Telegram notification
- Customer can call waiter or make a reservation via the Mini App

---

## 8. Restaurant Tables & QR Codes

### Current status

Restaurant tables are fully managed via the admin panel UI and REST API — no direct SQL required.

### How dine-in works

When a customer scans a table QR code, the Mini App calls:
```
GET /api/restaurants/{slug}/table/{table_number}
```
This returns the `table_id` from the database. The `table_id` is then sent with the order.

### Creating tables (required for dine-in)

**Option 1 — Admin panel UI (recommended):**
Open the restaurant admin panel → QR Codes tab → enter the number of tables → click Generate. Tables are saved to the database and QR code images are generated for download.

**Option 2 — API:**
```
POST /api/restaurants/me/tables
Authorization: Bearer <restaurant_admin_token>
Content-Type: application/json

{"table_count": 10}
```

**Option 3 — Delete a table:**
```
DELETE /api/restaurants/me/tables/{table_id}
Authorization: Bearer <restaurant_admin_token>
```

### QR URL format

Each QR code contains:
```
https://your-app.onrender.com/app?slug=pizza-house&table=1
```

When scanned, the Mini App resolves table number → database ID automatically.

---

## 9. Billing & Subscriptions

### What exists

The billing module tracks subscription plans and usage. It does **not** process payments — there is no payment gateway integration.

### Tables (created by migration 0005)

- `subscription_plans` — plan definitions (Free / Basic / Pro)
- `subscriptions` — per-restaurant subscriptions
- `usage_events` — order and product usage tracking

### Seed data

Migration 0005 automatically seeds three starter plans:

| Plan | Price | Orders/month | Products |
|---|---|---|---|
| Free | $0 | 100 | 20 |
| Basic | $29 | 500 | 100 |
| Pro | $79 | 2000 | 500 |

### Working endpoints

| Endpoint | Description |
|---|---|
| `GET /api/billing/plans` | List all plans |
| `GET /api/billing/subscription` | Current restaurant subscription |
| `POST /api/billing/subscribe` | Subscribe to a plan |
| `GET /api/billing/usage` | Current month usage |
| `GET /api/billing/invoice/{month}` | Generate PDF invoice |

### PDF invoices

Invoice generation requires `reportlab`:
```
pip install reportlab
```
It is included in `requirements.txt`. The invoice footer shows `PLATFORM_NAME`, `PLATFORM_URL`, `PLATFORM_EMAIL` from your environment.

### Current limitations

- No payment processing — billing is tracking only
- Paid plans return 402 (payment required) — only Free plan is fully functional out of the box
- No dunning, no automatic plan expiry

---

## 10. Cloudflare R2 Photo Storage

Required for dish photo upload. Without R2, the rest of the platform works normally but photo uploads return an error.

1. Cloudflare Dashboard → **R2** → **Create Bucket**
2. **Manage R2 API Tokens** → **Create Token** → **Object Read & Write** for your bucket
3. Set in Render environment:
   ```
   R2_ACCOUNT_ID=your-cloudflare-account-id
   R2_ACCESS_KEY_ID=your-key-id
   R2_SECRET_ACCESS_KEY=your-secret
   R2_BUCKET_NAME=restaurant-photos
   ```
4. For public file serving, configure a **Custom Domain** in R2 and set `R2_PUBLIC_URL`

---

## 11. Clean Install Verification

Run through this checklist after every fresh deployment:

```
[ ] GET /health → {"status": "healthy", "db": "ok"}
[ ] alembic current → 0005 (head)
[ ] 14 tables exist in DB (check in Neon dashboard)
[ ] Superadmin login works at /superadmin
[ ] Superadmin logout works (tests JWT revocation via revoked_tokens)
[ ] Agency created
[ ] Restaurant created
[ ] Menu category and item created
[ ] Telegram bot token + dispatcher set in restaurant settings
[ ] Telegram webhook registered (verified via getWebhookInfo)
[ ] Mini App opens in Telegram browser
[ ] Test order placed from Mini App
[ ] Order appears in restaurant admin panel
[ ] Telegram notification received by dispatcher
[ ] Order status changed in admin panel
[ ] Client Telegram notification received on status change
[ ] Reservation created
[ ] Waiter call created
[ ] GET /api/billing/subscription → 200
[ ] GET /api/billing/invoice/01 → PDF downloaded
[ ] Dish photo upload works (requires R2)
```

---

## 12. Known Limitations

Full list in `KNOWN_LIMITATIONS.md`. Key points:

| Area | Limitation |
|---|---|
| Tables API | Full UI (QR Codes tab) and API (`POST /api/restaurants/me/tables`) |
| Payments | No payment gateway — billing is tracking only |
| AI | Disabled by default — requires API key and `AI_ENABLED=true` |
| Scale | Sync SQLAlchemy, 1 Uvicorn worker — ~50 concurrent users |
| Notifications | Telegram only — no email, no SMS |
| Tests | SQLite in-memory — some PostgreSQL edge cases not covered |
| Backups | Not automated — configure separately |

---

## 13. What's Included in the Sale

**Included:**
- Full source code
- Alembic migration history (all 5 migrations)
- This Buyer's Guide
- `DEPLOYMENT.md` — deployment reference
- `ARCHITECTURE.md` — system design overview
- `API.md` — endpoint reference
- `SECURITY.md` — security model
- `KNOWN_LIMITATIONS.md` — honest list of current gaps
- CI/CD pipeline (GitHub Actions)
- Dockerfile + docker-compose for local development

**Not included:**
- Production data or client list
- Your Render / Neon / Cloudflare accounts (create your own)
- Telegram bot tokens (create via @BotFather)
- Ongoing support (negotiate separately if needed)

### Recommended first steps after purchase

1. Fork repository to your GitHub
2. Create Neon database + Render service
3. Generate all secret keys (Section 3)
4. Set all required environment variables
5. Deploy and verify `/health`
6. Create superadmin → agency → restaurant
7. Connect Telegram bot and place a test order end-to-end
8. Set up daily database backups (`pg_dump`)
9. Configure `ALLOWED_ORIGINS` with your domain
10. Set `PLATFORM_NAME`, `PLATFORM_URL`, `PLATFORM_EMAIL` for branded invoices

---

*Multi-Tenant White-Label Restaurant SaaS Platform*
*Built with FastAPI · PostgreSQL · Telegram Mini App · PWA*
