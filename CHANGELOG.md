# Changelog

All notable changes to Taomly are documented here.

---

## [2.1.1] — 2026-07-23

### Security
- **SEC-1** `analytics.py`: timezone value now passed as bound parameter `:tz` instead of f-string interpolation in raw SQL
- **SEC-2** `static/index.html`: added `esc()` HTML-escaping function; applied to server-supplied content rendered via `innerHTML` in the customer menu
- **SEC-4** `api.py`: added `ProxyHeadersMiddleware` to improve client IP detection behind reverse proxies
- **SEC-6** `routers/agency.py`: `verify_password()` now executes regardless of whether the agency email exists — reduces timing differences in login response
- **SEC-7** `config.py`: `MAX_INIT_DATA_AGE_SECONDS` default changed from `86400` to `3600` per Telegram recommendations

### Fixed
- **BUG-1** `routers/restaurants.py`: `is_popular` field now returns `p.is_popular` — previously returned `p.is_bestseller`, causing the "Popular" section in the Mini App to ignore the restaurant admin's selection
- **MIGRATION** `alembic/versions/0002_add_badge_columns.py`: corrected `down_revision` from `"0001"` to `"0001_initial"` — fixes `alembic upgrade head` on a fresh database

### Performance
- **PERF-1** `routers/superadmin.py`: dashboard now uses `CASE WHEN` aggregation — reduces database round-trips during dashboard loading

### DevOps
- `ci.yml`: added `audit` job that runs `pip-audit` on every push to check dependencies for known CVEs

### Documentation
- Added `BUYER_GUIDE.md` — step-by-step guide for deploying and taking ownership of the platform

---

## [1.3.0] — 2026-07-10

### Added
- Analytics schemas moved to schemas.py
- Billing schemas moved to schemas.py
- Alembic migration 0002: badge columns for products
- API.md, SECURITY.md, CHANGELOG.md, LICENSE

### Removed
- MIGRATION_badges.sql (replaced by Alembic 0002)

---

## [1.2.0] — 2026-07-05

### Added
- AI service layer (OpenRouter, OpenAI, Anthropic, Gemini)
- AI endpoints: generate-description, translate-menu, suggest-tags, generate-seo
- Landing page deployed to GitHub Pages (taomly-landing)

### Fixed
- get_current_restaurant → get_current_restaurant_admin in routers/ai.py
- Fernet key validation at startup in config.py
- database.py now uses settings.DATABASE_URL
- Rate limiting on /api/agency/register

---

## [1.1.0] — 2026-07-01

### Added
- Analytics Dashboard (5 endpoints, 5 period filters)
- Billing System (subscription_plans, subscriptions, usage_events)
- Billing plans: Free ($0), Basic ($29), Pro ($79)

### Fixed
- Circular import on limiter (moved to limiter.py)
- Missing email-validator package
- Missing DB columns: is_chef_choice, is_bestseller
- Password reset field in agency_admin.html

---

## [1.0.0] — 2026-06-25

### Added
- White Label Multi-Tenant architecture (Agency → Restaurant → User)
- Per-restaurant Telegram bot with HMAC-SHA256 verification
- Fernet encryption for bot tokens
- QR Management in admin panel
- Telegram notifications for order status (5 functions)
- White Label branding (applyBrandTheme, dynamic PWA manifest)
- Analytics, Billing, AI routers
- Full Alembic configuration
- 33+ pytest tests
- Docker setup
- ARCHITECTURE.md, README.md, ROADMAP.md
