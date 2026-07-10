# What's Included in the Sale

> **Note:** The exact scope of transferred assets is defined by the final
> Asset Purchase Agreement between the buyer and the seller.
> This document serves as a reference overview only.

---

## Source Code

- Complete Taomly source code
- Full Git history
- All branches (if applicable)

---

## Backend

- FastAPI application (`api.py`, all routers)
- SQLAlchemy models (`models.py`)
- Alembic migrations (versioned, production-ready)
- Authentication system (JWT, Fernet encryption, Telegram HMAC-SHA256)
- Multi-tenant architecture (Agency → Restaurant → End User)
- Analytics module (`routers/analytics.py` — 5 endpoints, 5 period filters)
- Billing module (`routers/billing.py` — subscription plans, usage tracking)
- AI endpoints (`routers/ai.py` — architecture ready, provider-agnostic)
- Rate limiting (`slowapi`)
- Error monitoring integration (`sentry-sdk`)

---

## Frontend

- Restaurant client Mini App / PWA (`static/index.html`)
- Restaurant Admin Panel (`static/admin.html`)
- Agency Admin Panel (`static/agency_admin.html`)
- PWA assets (manifest, service worker, icons)
- Offline fallback page (`static/offline.html`)

---

## Documentation

- `README.md` — project overview and quick start
- `API.md` — endpoint reference
- `ARCHITECTURE.md` — system design and component overview
- `SECURITY.md` — security model and recommendations
- `DEPLOYMENT.md` — step-by-step deployment guide (Render + Neon)
- `CHANGELOG.md` — version history
- `ROADMAP_STAGE1.md` — completed stage roadmap
- `LICENSE` — license file
- `.env.example` — environment variable reference

---

## Infrastructure

- `Dockerfile` (multi-stage, production-ready)
- `docker-compose.yml` (local dev + migration profile)
- Render deployment configuration
- CI/CD pipeline (`.github/workflows/ci.yml`)
- Pre-commit hooks (`.pre-commit-config.yaml`)

---

## Database

- Full PostgreSQL schema via SQLAlchemy models
- Alembic migration history (versioned, reproducible)
- Tables: Agency, Restaurant, Category, Product, Order, OrderItem,
  WaiterCall, Reservation, SubscriptionPlan, Subscription, UsageEvent

---

## Testing

- Test suite (`tests/` — 7 test files)
- `pytest` configuration (`pyproject.toml`)
- SQLite in-memory test database (no external dependencies for CI)
- Fixtures covering: auth, multi-tenancy, orders, analytics, billing, AI, schemas

---

## Rights Transferred

The scope of rights (including modification, rebranding, white-label use,
and commercial exploitation) is defined exclusively by the final
Asset Purchase Agreement between the buyer and the seller.

---

## Not Included

- Production server or hosting account
- Production database or any customer data
- Third-party API keys (OpenRouter, OpenAI, Anthropic, Gemini, Sentry)
- Telegram bot tokens or BotFather accounts
- Personal accounts (Render, Neon, GitHub)
- Email accounts or domain names (unless explicitly stated in the Agreement)
- Paid service subscriptions
- Any future updates or support (unless explicitly agreed)
