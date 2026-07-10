# Changelog

All notable changes to Taomly are documented here.

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
