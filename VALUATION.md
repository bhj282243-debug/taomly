# Taomly — Asset Overview & Valuation Guide

This document describes what is included in the sale of Taomly,
the target market, competitive positioning, and factors that
influence valuation. Intended for use in M&A negotiations.

---

## What You Are Buying

Taomly is a **white-label multi-tenant restaurant SaaS platform** built
on a modern, production-ready stack. You are acquiring the full source
code, documentation, and deployment infrastructure — not a running
business with clients.

### Included in the Sale

| Asset | Details |
|---|---|
| Full source code | FastAPI backend, 4 SPA frontends, PWA |
| Database schema | PostgreSQL via Alembic migrations |
| Documentation | ARCHITECTURE.md, API.md, SECURITY.md, DEPLOYMENT.md, BUYER_GUIDE.md |
| CI/CD pipeline | GitHub Actions (lint, test, security audit, docker build) |
| Docker configuration | Multi-stage build, production-ready |
| MIT License | No restrictions on commercial use or resale |
| Roadmap | 4-stage product development plan |

### Not Included

| Item | Notes |
|---|---|
| Active clients | Zero — platform has not been sold to restaurants yet |
| MRR / ARR | Zero — no recurring revenue at time of sale |
| Render account | Buyer creates their own |
| Neon account | Buyer creates their own |
| Telegram bots | Buyer creates per restaurant via BotFather |
| Ongoing support | Negotiated separately if required |

---

## Technical Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI 0.115, SQLAlchemy 2.0 |
| Database | PostgreSQL 15+ (Neon recommended) |
| Auth | JWT (python-jose), bcrypt, Fernet-128 |
| Telegram | pyTelegramBotAPI, HMAC-SHA256 initData verification |
| Frontend | Vanilla JS SPA × 4, PWA, Service Worker |
| DevOps | Docker, GitHub Actions, Render |
| Monitoring | Sentry (optional) |
| Security | slowapi rate limiting, ProxyHeadersMiddleware, CSP headers |

---

## Business Model

The platform is designed for an **agency/reseller model**:

1. You (platform owner) sign up digital agencies as resellers
2. Each agency brings 10–20 restaurants at $30–50/month per restaurant
3. You collect platform fee per restaurant per month

**Example unit economics at scale:**

| Metric | Conservative | Optimistic |
|---|---|---|
| Agencies | 5 | 20 |
| Restaurants per agency | 8 | 15 |
| Total restaurants | 40 | 300 |
| Monthly fee per restaurant | $30 | $50 |
| **MRR** | **$1,200** | **$15,000** |
| **ARR** | **$14,400** | **$180,000** |

These are projections, not guarantees. Actual results depend on sales execution.

---

## Target Markets

**Primary:** Uzbekistan — high Telegram penetration, growing restaurant market,
limited competition in QR-ordering SaaS.

**Secondary CIS:** Kazakhstan, Kyrgyzstan, Azerbaijan — similar Telegram usage
patterns, similar restaurant digitalization gap.

**International:** Any market where Telegram Mini Apps are used for commerce.

---

## Competitive Positioning

| Feature | Taomly | Generic QR Menu SaaS |
|---|---|---|
| Telegram Mini App (native) | ✅ | ❌ Most use web links |
| Per-restaurant Telegram bot | ✅ | ❌ |
| White-label (own branding) | ✅ | Partial |
| Agency/reseller model | ✅ | Rare |
| Multi-tenant architecture | ✅ | Varies |
| Self-service menu management | ✅ | ✅ |
| Open source stack (no vendor lock) | ✅ | Varies |
| Source code included in sale | ✅ | ❌ SaaS only |

---

## Valuation Factors

### Factors That Increase Value

- Clean, audited codebase with zero critical security issues
- Full documentation (architecture, API, security, deployment, buyer guide)
- MIT license — no restrictions on resale or white-labeling
- Working CI/CD pipeline with automated security audit
- Modular architecture — easy to extend or customize per market
- AI layer already stubbed — low effort to activate
- Telegram Mini App — growing distribution channel, low acquisition cost

### Factors That Reduce Value

- Zero MRR at time of sale — no proven revenue
- Zero clients — requires sales effort from buyer
- Synchronous SQLAlchemy — limits concurrency (documented, fixable in Stage 2)
- No payment gateway — billing module tracks invoices only
- Single-worker deployment on Render Free tier

### Price Anchors

The fair value of a SaaS codebase without revenue is typically assessed as:

- **Development cost replacement:** 800–1,200 hours of senior development
  at market rates — this code base represents $40,000–$80,000 in development value
- **Strategic discount:** Buyer pays for code, not proven revenue
- **Negotiable range:** Depends on buyer's ability to execute sales

---

## Risk Factors for Buyer

| Risk | Mitigation |
|---|---|
| No clients → requires sales effort | Agency model lowers barrier — one agency = 10+ restaurants |
| Technical limitations at scale | Documented in KNOWN_LIMITATIONS.md with clear resolution path |
| Telegram platform dependency | PWA mode works without Telegram — dual distribution |
| CIS market knowledge required | Localization already done for UZ/RU |
| Support after sale | Negotiate transition support period separately |

---

## Recommended First 90 Days for Buyer

**Days 1–14:** Deploy, verify, run end-to-end test order
**Days 15–30:** Find first agency partner (digital studio, marketing agency)
**Days 31–60:** Onboard 3–5 restaurants via the agency
**Days 61–90:** Collect feedback, fix friction points, onboard second agency

First MRR target: **$300–500/month** (10 restaurants × $30–50)

---

## What Makes This a Strong Asset

1. **Finished product** — not a prototype. Orders flow end-to-end, Telegram notifications work, admin panels are complete.
2. **Honest documentation** — known limitations are documented, not hidden.
3. **Security audit completed** — zero critical issues confirmed by independent review.
4. **Clear growth path** — 4-stage roadmap with concrete next steps.
5. **Low operational cost** — Render Free + Neon Free = $0/month until revenue justifies upgrade.

---

*Taomly v2.1.1 — White Label Multi-Tenant Restaurant SaaS Platform*
*Stack: FastAPI · PostgreSQL · Telegram Mini App · PWA · MIT License*
