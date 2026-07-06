# Taomly — White Label Restaurant SaaS Platform

Multi-tenant restaurant ordering system built on Telegram Mini App.
Each restaurant gets its own Telegram bot, menu, branding and admin panel.

## Architecture

```
Agency Owner
  └── Restaurant A (bot @chinar_bot, slug: chinar)
  └── Restaurant B (bot @palace_bot, slug: palace)
        └── Categories → Products
        └── Orders (delivery / takeaway / dine_in)
        └── Reservations
        └── Waiter Calls
        └── Tables (QR per table)
```

**Stack:** FastAPI · SQLAlchemy (sync) · Neon PostgreSQL · Render · Telegram Mini App · JWT · Fernet · slowapi · Sentry

**Repo:** `bhj282243-debug/taomly`
**Live:** `https://taomly.onrender.com`

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✅ | Neon PostgreSQL connection string |
| `SECRET_KEY` | ✅ | JWT signing key (min 32 chars random string) |
| `FERNET_KEY` | ✅ | Fernet encryption key for bot tokens. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `WEBHOOK_URL` | ✅ | Base URL of deployed app, e.g. `https://taomly.onrender.com` |
| `BOT_TOKEN` | ✅ | Platform Telegram bot token |
| `SENTRY_DSN` | optional | Sentry DSN for error monitoring |
| `WEBHOOK_SECRET` | optional | Auto-derived from SECRET_KEY if not set |
| `ALLOWED_ORIGINS` | optional | Comma-separated CORS origins. Empty = allow all (dev mode) |
| `ACCESS_TOKEN_EXPIRE_HOURS` | optional | JWT TTL in hours (default: 24) |
| `MAX_INIT_DATA_AGE_SECONDS` | optional | Telegram initData max age (default: 86400) |

---

## Quick Start (local)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env file with required variables (see above)
cp .env.example .env

uvicorn api:app --reload
```

---

## URL Structure

| URL | Description |
|-----|-------------|
| `/app?slug={slug}` | Customer Mini App for restaurant |
| `/admin` | Restaurant admin panel |
| `/agency-admin` | Agency owner dashboard |
| `/api/agency/login` | Agency login |
| `/api/agency/restaurant-login` | Restaurant admin login |
| `/api/menu/{restaurant_id}` | Public menu |
| `/api/orders/` | Create order (Telegram auth) |
| `/webhook/{slug}` | Per-restaurant Telegram webhook |
| `/health` | Health check (Render) |

---

## Multi-Tenant Security

- Every request to `/api/orders/`, `/api/menu/`, etc. requires `X-Restaurant-Id` header
- Telegram `initData` verified with HMAC-SHA256 using **each restaurant's own bot token**
- JWT contains `restaurant_id` + `agency_id` — all queries filtered by tenant
- IDOR prevention: resource ownership verified against JWT on every mutating endpoint
- Rate limiting: 10 req/min on login endpoints, 120 req/min on API

---

## Database Migrations

No Alembic (no terminal access on Render free tier).
New columns added via Neon SQL Editor with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

Migration files:
- `MIGRATION_badges.sql` — Product badge columns (is_bestseller, is_new, is_spicy, is_chef_choice)

---

## Tech Debt (Stage 2)

| ID | Description |
|----|-------------|
| TD-1 | Sync SQLAlchemy — migrate to AsyncSession before Stage 2 |
| TD-2 | `_BOT_CACHE` in-process dict — use Redis before scaling to 2+ workers |
| TD-3 | Webhook accepts raw `dict` — add Pydantic TelegramUpdate schema |
| TD-6 | Telegram bot messages hardcoded in Uzbek — add i18n |
| TD-8 | Static `manifest.json` — make dynamic per restaurant branding |

---

## Test Credentials

| | |
|---|---|
| Agency | `admin@taomly.uz` / `12345678` |
| Restaurant chinar | slug: `chinar`, password: `secret` |
| Restaurant test-2 | slug: `taomly-test-2`, password: `secret` |
| Dispatcher Telegram ID | `331294063` |

---

## Price Format

All prices stored as **integer sums (UZS)**. Example: `45000` = 45 000 so'm.
Display as: `f"{price:,} so'm"`
