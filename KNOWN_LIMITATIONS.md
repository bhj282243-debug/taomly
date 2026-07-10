# Known Limitations

This document honestly describes the current limitations of the Taomly platform.
It is intended to help the buyer make an informed decision.

---

## Payments

- No payment gateway is integrated (no Stripe, Payme, Click, or similar)
- The billing module demonstrates subscription plans and usage tracking,
  but does not process real transactions
- Payment integration must be implemented by the buyer

---

## AI Features

- AI endpoints are architecture-ready but require a third-party API key to function
- Supported providers: OpenRouter, OpenAI, Anthropic, Gemini
- AI is disabled by default (`AI_ENABLED=false`)
- No AI features work out of the box without configuration

---

## Database

- SQLAlchemy is synchronous (not async)
- Suitable for current load; migration to AsyncSession is recommended
  before scaling beyond ~50 concurrent users
- No production data is included — the buyer receives an empty database schema

---

## Infrastructure

- Designed and tested on Render (free tier) + Neon (free tier)
- No Redis, Celery, or background task queue (Telegram notifications
  use FastAPI `BackgroundTasks` — suitable for low-to-medium load)
- No automatic database backups configured (must be set up by the buyer)

---

## Onboarding

- No self-service onboarding for new restaurants
- Agency admin must manually register each restaurant via the Agency Admin Panel
- No automated email notifications or welcome flows

---

## Telegram

- Each restaurant requires its own Telegram bot (created via @BotFather)
- Bot tokens are managed by the agency admin, not automatically provisioned
- Telegram notifications are in Uzbek by default

---

## Testing

- Test suite uses SQLite in-memory (not PostgreSQL)
- Some edge cases specific to PostgreSQL behavior may not be covered
- No end-to-end browser tests (no Playwright or Selenium)

---

## Production Readiness

- The platform has not been tested under production load with real customers
- No uptime history or SLA data available
- Sentry integration is included but requires a separate Sentry account and DSN

---

## Out of Scope

- No mobile native app (iOS / Android) — web PWA only
- No marketplace or multi-vendor ordering
- No inventory management
- No staff management or scheduling
- No loyalty or points system
