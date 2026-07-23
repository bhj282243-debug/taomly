# Taomly — Product Roadmap

This document outlines the planned development path for the Taomly platform.
It is intended for buyers, investors, and technical evaluators.

Current version: **2.1.1** — Stage 1 complete.

---

## What is Already Built (Stage 1 — Complete)

The platform is fully functional as a white-label multi-tenant restaurant SaaS.

**Core:**
- Agency → Restaurant → Customer three-tier architecture
- Per-restaurant Telegram Mini App (PWA) with QR-code table ordering
- Order lifecycle management (new → confirmed → preparing → ready → delivered)
- Telegram notifications for every order status change
- Restaurant self-service: menu management, categories, products, photos, prices

**Security:**
- HMAC-SHA256 Telegram initData verification
- Fernet-encrypted bot tokens in database
- JWT authentication (agency and restaurant layers)
- bcrypt password hashing
- Rate limiting on all auth endpoints
- SSRF protection on URL inputs
- Security headers (CSP, X-Frame-Options, Referrer-Policy)

**Admin panels:**
- SuperAdmin Console: agencies, restaurants, MRR, platform metrics
- Agency Admin Panel: restaurant creation, management, settings
- Restaurant Admin Panel: orders, menu, QR codes, statistics, analytics

**Infrastructure:**
- Multi-stage Docker build, non-root user
- Alembic migrations
- Sentry error monitoring
- GitHub Actions CI (lint, test, security audit, docker build)
- Render deployment with health checks

---

## Stage 2 — Growth (Next Owner's Priority)

These features directly increase revenue per restaurant and platform stickiness.

### 2.1 Payments
- Integrate Click, Payme (Uzbekistan), or Stripe (international)
- Order payment status tracking
- Revenue split between agency and platform (configurable %)

### 2.2 Real-time Orders
- Replace polling with WebSockets or Server-Sent Events
- Instant order arrival in admin panel without page refresh
- Kitchen Display System (KDS) view

### 2.3 AI Assistant
- AI endpoint stubs already exist (`routers/ai.py`, `ai_service.py`)
- Activate with `AI_ENABLED=true` and OpenRouter/OpenAI API key
- Dish description generation (UZ/RU)
- Menu translation
- Suggested tags and badges

### 2.4 Async SQLAlchemy
- Replace sync SQLAlchemy with async version
- Enable multiple Uvicorn workers
- Required before scaling beyond ~50 concurrent users per restaurant

### 2.5 Redis
- Session caching
- Bot token cache (currently in-process dict — breaks with multiple workers)
- Rate limit storage (currently in-memory — resets on redeploy)

---

## Stage 3 — Scale

### 3.1 Loyalty & CRM
- Customer purchase history
- Loyalty points system
- Push notifications via Telegram

### 3.2 Analytics Expansion
- Peak hours dashboard (already built — `routers/analytics.py`)
- Revenue by category, average check trends
- Customer return rate

### 3.3 Delivery Integration
- Courier assignment
- Delivery zone management
- Integration with Yandex.Delivery or local courier services

### 3.4 Multi-language
- UZ / RU / EN menu display
- Customer language detection from Telegram `language_code`
- Admin panel localization

### 3.5 Reservations
- Table reservation flow (endpoint exists — `routers/reservations.py`)
- Calendar view in admin panel
- Reminder notifications

---

## Stage 4 — Platform

### 4.1 Marketplace
- Public restaurant directory
- Shared loyalty program across restaurants
- Network effects: one Telegram account — all restaurants

### 4.2 Developer API
- Public REST API for third-party integrations
- Webhooks for external systems (accounting, inventory, POS)
- API key management in admin panel

### 4.3 White Label CIS Expansion
- Kazakhstan, Kyrgyzstan, Azerbaijan markets
- Localized payment providers per country
- Agency partner program with revenue sharing

### 4.4 Taomly Intelligence
- Platform-wide anonymized data analysis
- Per-restaurant AI recommendations based on network patterns
- Demand forecasting, inventory suggestions

---

## Known Technical Debt (Accepted at Stage 1)

Full details in [KNOWN_LIMITATIONS.md](./KNOWN_LIMITATIONS.md).

| Item | When to Address |
|---|---|
| Synchronous SQLAlchemy | Stage 2 (before scaling) |
| In-process bot token cache | Stage 2 (before multi-worker) |
| Redis for rate limiting | Stage 2 |
| No payment gateway | Stage 2 |
| SQLite used in tests (not PostgreSQL) | Stage 2 |
| Hardcoded Uzbek language in notifications | Stage 3 |

---

## Architecture Principle

> One engine — different styles.

The platform is designed so that each restaurant can have its own branding, bot, and domain — while sharing the same backend infrastructure. Adding a new restaurant takes under 5 minutes and requires no developer involvement.

---

*Taomly v2.1.1 — Last updated: 2026-07-23*
