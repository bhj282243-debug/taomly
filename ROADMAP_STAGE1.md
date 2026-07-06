# TAOMLY — STAGE 1 ROADMAP & AUDIT CHECKLIST

> CTO/Principal Architect mode. Target: Premium White Label Multi-Tenant SaaS,
> investment-ready. No AI, no Marketplace, no Billing yet.
> Cycle: Fix → Re-audit → Fix → … until zero Critical/High.

---

## LEGEND
- ✅ Done
- 🔄 In Progress
- ⬜ Pending
- 📝 Documented (accepted tech debt)

---

## 🔴 CRITICAL

| # | Problem | File(s) | Status |
|---|---------|---------|--------|
| C-1 | CORS not configured — entire API open to cross-origin requests | `api.py` | ✅ Done |
| C-2 | No Rate Limiting — login endpoints open to brute-force | `api.py`, `routers/agency.py` | ✅ Done |
| C-3 | JWT stored in `sessionStorage` in agency_admin.html — XSS steals agency token | `static/agency_admin.html` | ✅ Done |
| C-4 | `requirements.txt` has no pinned versions — unpredictable builds | `requirements.txt` | ✅ Done |

---

## 🟠 HIGH

| # | Problem | File(s) | Status |
|---|---------|---------|--------|
| H-1 | No security headers (CSP, X-Frame-Options, etc.) | `api.py` | ✅ Done |
| H-2 | N+1 / full table scan in public menu endpoint | `routers/restaurants.py` | ✅ Done |
| H-3 | Synchronous SQLAlchemy blocks async FastAPI event loop | `database.py`, all routers | 📝 Tech Debt |
| H-4 | `_BOT_CACHE` in-process dict breaks with multiple workers | `handlers.py` | 📝 Tech Debt |
| H-5 | PWA completely missing (manifest, service worker, install prompt) | `static/index.html` | ✅ Done |
| H-6 | No Sentry error monitoring — production errors invisible | `api.py`, `requirements.txt` | ✅ Done |

---

## 🟡 MEDIUM

| # | Problem | File(s) | Status |
|---|---------|---------|--------|
| M-1 | WEBHOOK_SECRET logic duplicated in `api.py` and `agency.py` | `api.py`, `routers/agency.py` | ✅ Done |
| M-2 | Product badges (#bestseller etc.) stored in description text — anti-pattern | `models.py`, `schemas.py`, `index.html` | ✅ Done |
| M-3 | `OrderResponse` missing `updated_at` — no status change timestamp | `schemas.py` | ✅ Done |
| M-4 | Duplicate `VALID_STATUS_TRANSITIONS` in orders, waiter_calls, reservations | 3 routers | ✅ Done |
| M-5 | `GET /api/restaurants/{slug}` embeds full menu — doubles `/api/menu/` | `routers/restaurants.py` | 📝 Documented |
| M-6 | `agency_admin.html` active nav item missing contrast fix | `static/agency_admin.html` | ✅ Done (in C-3 fix) |
| M-7 | No `robots.txt` / `sitemap.xml` — crawlers index admin panels | `static/` | ✅ Done |
| M-8 | `README.md` is empty | `README.md` | ✅ Done |
| M-9 | `ReservationCreate` missing future date validation | `schemas.py` | ✅ Done |
| M-10 | `OrderCreate` missing delivery address validation when order_type=delivery | `schemas.py`, `routers/orders.py` | ✅ Done |
| M-11 | No `updated_at` on `users` table | `models.py` | 📝 Tech Debt |
| M-12 | `client_phone` no format validation anywhere | `schemas.py` | ✅ Done |
| M-13 | `handlers.py` hardcodes Uzbek language (no i18n hook) | `handlers.py` | 📝 Tech Debt |
| M-14 | `notify_new_order` shows `table_id` (integer) instead of `table_number` (human-readable) | `handlers.py` | ✅ Done |
| M-15 | No `config.py` — env vars scattered across 4 files | multiple files | ✅ Done |

---

## 🟢 LOW

| # | Problem | File(s) | Status |
|---|---------|---------|--------|
| L-1 | Webhook endpoints accept raw `dict` — no Pydantic schema validation | `api.py` | 📝 Tech Debt |
| L-2 | `Product.price` type undocumented (tiyins? sums?) | `models.py` | ✅ Done |
| L-3 | `AgencyRegister` no max_length on name/password fields | `schemas.py` | ✅ Done |
| L-4 | `RestaurantCreate.name` no max_length validation | `schemas.py` | ✅ Done |
| L-5 | `/health` endpoint not protected — reveals DB status to public | `api.py` | 📝 Tech Debt |
| L-6 | `platform_bot` `/start` handler hardcodes `taomly.onrender.com` URL | `handlers.py` | ✅ Done |
| L-7 | No HTTP→HTTPS redirect enforcement | `api.py` | 📝 Tech Debt (Render handles) |
| L-8 | `photo_url` / `logo_url` accept any string — no URL validation | `schemas.py` | ✅ Done |
| L-9 | `admin.html` and `agency_admin.html` lack `<meta name="viewport">` responsive fixes | HTML files | ✅ Done (already present) |
| L-10 | No favicon for admin panels | `static/` | ✅ Done |

---

## PWA CHECKLIST

| Item | Status |
|------|--------|
| `manifest.json` | ✅ Done |
| Service Worker (`sw.js`) | ✅ Done |
| Offline fallback page | ✅ Done |
| Install prompt (`beforeinstallprompt`) | ✅ Done |
| Icons 192×192 and 512×512 (SVG) | ✅ Done |
| `<link rel="manifest">` in index.html | ✅ Done |
| `theme-color` meta tag | ✅ Done |
| Lighthouse PWA score target: 95+ | ⬜ Verify after deploy |

---

## WHITE LABEL CHECKLIST

| Item | Status |
|------|--------|
| Per-restaurant primary/secondary/accent colors | ✅ Done |
| Per-restaurant logo_url | ✅ Done |
| Per-restaurant welcome_text | ✅ Done |
| Per-restaurant Telegram bot | ✅ Done |
| Custom domain field in DB | ✅ Done |
| CSS variables applied from DB at runtime | ✅ Done |
| Restaurant branding in PWA manifest (dynamic) | 📝 Tech Debt (static manifest) |
| Per-restaurant favicon | 📝 Tech Debt |

---

## ARCHITECTURE CHECKLIST

| Item | Status |
|------|--------|
| Multi-tenant data isolation (JWT-based) | ✅ Done |
| IDOR prevention on all endpoints | ✅ Done |
| Fernet-encrypted bot tokens | ✅ Done |
| HMAC-SHA256 Telegram initData verification | ✅ Done |
| Replay attack protection (auth_date TTL) | ✅ Done |
| Centralized config module | ✅ Done |
| Status machine for orders/reservations/waiter_calls | ✅ Done |
| Soft delete for restaurants | ✅ Done |
| DB indexes on hot query paths | ✅ Done |
| Async SQLAlchemy | 📝 Stage 2 tech debt |

---

## TECH DEBT REGISTER (Accepted for Stage 2)

| ID | Description | Impact | When to Fix |
|----|-------------|--------|-------------|
| TD-1 | Sync SQLAlchemy blocks event loop | Performance at scale | Before Stage 2 (AI) |
| TD-2 | `_BOT_CACHE` breaks with 2+ workers | Correctness at scale | Before scaling Render |
| TD-3 | Webhook accepts raw `dict` (no Pydantic) | Minor security | Stage 2 |
| TD-4 | `/health` endpoint public | Info disclosure | Stage 2 |
| TD-5 | HTTP→HTTPS not enforced in app (Render handles it) | None currently | N/A |
| TD-6 | i18n hardcoded to Uzbek in handlers.py | White Label completeness | Stage 2 |
| TD-7 | `GET /api/restaurants/{slug}` duplicates menu data | Minor over-fetching | Stage 2 refactor |
| TD-8 | Dynamic PWA manifest per restaurant branding | Premium White Label | Stage 2 |
| TD-9 | `users` table missing `updated_at` | Minor audit gap | Stage 2 |

---

## STAGE 1 COMPLETION CRITERIA

- [ ] Zero Critical issues
- [ ] Zero High issues
- [ ] All Medium: fixed or documented
- [ ] Architecture: scalable foundation confirmed
- [ ] Security: modern standards met
- [ ] White Label: fully functional
- [ ] PWA: fully implemented
- [ ] Project looks and feels like Premium SaaS

---

## PROGRESS LOG

| Date | Step | Files Changed |
|------|------|---------------|
| 2026-07-05 | Audit completed, ROADMAP created | ROADMAP_STAGE1.md |
| 2026-07-05 | C-1: CORS + C-2: Rate Limiting + H-1: Security Headers + M-1: config.py | `config.py`, `api.py`, `requirements.txt` |
| 2026-07-05 | C-3: agency_admin.html JWT in memory + M-6: nav contrast | `static/agency_admin.html` |
| 2026-07-05 | C-4: pinned requirements.txt | `requirements.txt` |
| 2026-07-05 | H-2: SQL filter for available products | `routers/restaurants.py` |
| 2026-07-05 | H-5: PWA (manifest + sw.js + index.html registration) | `static/manifest.json`, `static/sw.js`, `static/index.html` |
| 2026-07-05 | H-6: Sentry integration | `api.py`, `config.py`, `requirements.txt` |
| 2026-07-05 | M-2: Product badge columns (SQL migration script) | `models.py`, `schemas.py`, `MIGRATION_badges.sql` |
| 2026-07-05 | M-3+M-10+M-9+M-12+L-3+L-4+L-8: Schema improvements | `schemas.py` |
| 2026-07-05 | M-4: Centralized status transitions | `routers/orders.py`, `routers/waiter_calls.py`, `routers/reservations.py` |
| 2026-07-05 | M-7: robots.txt + favicon | `static/robots.txt`, `static/favicon.svg` |
| 2026-07-05 | M-8: README.md | `README.md` |
| 2026-07-05 | M-14: table_number in Telegram notifications | `handlers.py` |
| 2026-07-05 | L-2: Price documentation + L-6: platform bot URL fix | `models.py`, `handlers.py` |

---

*Last updated: 2026-07-05 — Stage 1 in progress*
