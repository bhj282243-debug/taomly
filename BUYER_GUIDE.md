# Taomly — Buyer's Guide

Complete step-by-step guide for deploying, configuring, and taking ownership of the Taomly platform.

---

## Table of Contents

1. [Requirements](#1-requirements)
2. [Environment Variables](#2-environment-variables)
3. [Database Setup & Migrations](#3-database-setup--migrations)
4. [Deploy to Render](#4-deploy-to-render)
5. [Create SuperAdmin](#5-create-superadmin)
6. [Create First Agency](#6-create-first-agency)
7. [Create First Restaurant](#7-create-first-restaurant)
8. [Connect Telegram Bot](#8-connect-telegram-bot)
9. [Verify Everything Works](#9-verify-everything-works)
10. [Database Backups](#10-database-backups)
11. [Known Limitations](#11-known-limitations)
12. [Support & Handover](#12-support--handover)

---

## 1. Requirements

| Component | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.12+ also supported |
| PostgreSQL | 15+ | Neon (free tier) recommended |
| Render | Free or Starter | For hosting the backend |
| Telegram | Any | One bot per restaurant |
| Git | Any | To clone the repository |

**Third-party accounts you will need:**

- [Neon](https://neon.tech) — free PostgreSQL database
- [Render](https://render.com) — free web service hosting
- [Sentry](https://sentry.io) — optional, for error monitoring
- [Telegram](https://t.me/BotFather) — to create bots for restaurants

---

## 2. Environment Variables

Create these variables in Render → Your Service → Environment.

### Required

```
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
SECRET_KEY=<random 64-char string>
FERNET_KEY=<Fernet key — see generation below>
SUPERADMIN_EMAIL=superadmin@yourdomain.com
SUPERADMIN_PASSWORD_HASH=<bcrypt hash — see generation below>
WEBHOOK_URL=https://your-app.onrender.com
```

### Optional

```
BOT_TOKEN=<platform-level Telegram bot token>
SENTRY_DSN=<your Sentry DSN>
ALLOWED_ORIGINS=https://yourdomain.com,https://your-app.onrender.com
MAX_INIT_DATA_AGE_SECONDS=3600
ACCESS_TOKEN_EXPIRE_HOURS=8
RATE_LIMIT_LOGIN=10/minute
RATE_LIMIT_SUPERADMIN_LOGIN=5/minute
BUILD_HASH=<set automatically by Render on each deploy>
```

### Generating Secret Keys

**SECRET_KEY** (run once, save the result):
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**FERNET_KEY** (run once, save the result):
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**SUPERADMIN_PASSWORD_HASH** (replace `yourpassword` with your chosen password):
```bash
python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('yourpassword'))"
```

> ⚠️ Store all keys securely. If `FERNET_KEY` is lost, encrypted bot tokens in the database cannot be recovered.

---

## 3. Database Setup & Migrations

### Step 1 — Create a Neon database

1. Sign up at [neon.tech](https://neon.tech)
2. Create a new project
3. Copy the connection string (format: `postgresql://user:pass@host/dbname?sslmode=require`)
4. Set it as `DATABASE_URL` in Render

### Step 2 — Run migrations

Migrations are managed by Alembic. On Render, they run automatically via the Start Command:

```
alembic upgrade head && uvicorn api:app --host 0.0.0.0 --port $PORT --workers 1
```

To run manually from your local machine:

```bash
git clone https://github.com/your-org/taomly.git
cd taomly
pip install -r requirements.txt
export DATABASE_URL=postgresql://...
alembic upgrade head
```

### Migration chain

```
0001_initial → 0002_add_badge_columns → 0003_add_is_popular
```

All three migrations must apply cleanly. Verify:

```bash
alembic current
# Expected: 0003 (head)
```

---

## 4. Deploy to Render

1. Fork or transfer the repository to your GitHub account
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repository
4. Configure:
   - **Environment:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `alembic upgrade head && uvicorn api:app --host 0.0.0.0 --port $PORT --workers 1 --loop uvloop`
5. Add all environment variables from Section 2
6. Click **Deploy**

### Verify deployment

```
https://your-app.onrender.com/health
```

Expected response:
```json
{"status": "healthy", "db": "ok"}
```

---

## 5. Create SuperAdmin

The SuperAdmin account is configured via environment variables — there is no registration endpoint.

1. Set `SUPERADMIN_EMAIL` and `SUPERADMIN_PASSWORD_HASH` in Render (see Section 2)
2. Redeploy the service
3. Open `https://your-app.onrender.com/superadmin`
4. Log in with the email and password you chose

From the SuperAdmin Console you can:
- Create and manage agencies
- View all restaurants across the platform
- Freeze/unfreeze restaurants
- View MRR and platform metrics
- Impersonate any agency

---

## 6. Create First Agency

An agency is a reseller — a digital studio or individual that manages multiple restaurants.

### Via SuperAdmin Console (UI)

1. Open `/superadmin` → **Agencies** → **+ New Agency**
2. Fill in: name, email, password
3. Click **Create**

### Via API

```bash
curl -X POST https://your-app.onrender.com/api/superadmin/agencies \
  -H "Authorization: Bearer <superadmin_token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Agency", "email": "agency@example.com", "password": "securepass123"}'
```

The agency owner logs in at `/agency-admin` with their email and password.

---

## 7. Create First Restaurant

Restaurants are created by agency owners from the Agency Admin Panel.

### Via Agency Admin Panel (UI)

1. Open `/agency-admin` → log in as the agency owner
2. Click **+ New Restaurant**
3. Fill in:
   - Name
   - Slug (URL identifier, e.g. `pizza-house`) — must be unique
   - Admin password (for restaurant staff)
   - Telegram Bot Token (from BotFather — see Section 8)
   - Telegram Dispatcher ID (your Telegram user ID — receives order notifications)
4. Click **Create**

### Restaurant admin panel

Restaurant staff log in at:
```
https://your-app.onrender.com/admin?slug=your-restaurant-slug
```

Password: set during restaurant creation.

### Customer-facing Mini App

Customers order via:
```
https://your-app.onrender.com/app?slug=your-restaurant-slug
```

Or via Telegram:
```
https://t.me/your_bot?start=your-restaurant-slug
```

---

## 8. Connect Telegram Bot

Each restaurant needs its own Telegram bot.

### Step 1 — Create a bot

1. Open Telegram → search `@BotFather`
2. Send `/newbot`
3. Choose a name and username (must end in `bot`)
4. Copy the token (format: `1234567890:AAF...`)

### Step 2 — Get your Telegram user ID

1. Open Telegram → search `@userinfobot`
2. Send `/start`
3. Copy your numeric ID (e.g. `331294063`)

### Step 3 — Set up the bot in the Agency Admin Panel

1. Open `/agency-admin` → select the restaurant → **Settings**
2. Paste the Bot Token and your Telegram ID
3. Save

The platform automatically registers a webhook for the bot. You will receive a Telegram notification for every new order.

### Step 4 — Test

Place a test order via the Mini App. You should receive a Telegram message within 2–3 seconds.

---

## 9. Verify Everything Works

Run through this checklist after deployment:

```
[ ] GET /health returns {"status": "healthy", "db": "ok"}
[ ] SuperAdmin can log in at /superadmin
[ ] Agency can log in at /agency-admin
[ ] Restaurant admin can log in at /admin?slug=...
[ ] Menu loads at /app?slug=...
[ ] Test order can be placed
[ ] Telegram notification received after order
[ ] Order status can be changed in admin panel
[ ] Client receives Telegram notification on status change
```

---

## 10. Database Backups

Neon provides automatic backups on paid plans. On the free tier, set up manual backups.

### Manual backup (run daily via cron or manually)

```bash
pg_dump $DATABASE_URL > taomly_backup_$(date +%Y%m%d).sql
```

### Restore

```bash
psql $DATABASE_URL < taomly_backup_20260101.sql
```

### Recommended

- Enable Neon's point-in-time restore on the Launch plan ($19/month)
- Or export daily to S3/Backblaze via a simple cron script

> ⚠️ No automated backup is configured out of the box. Set this up before onboarding real clients.

---

## 11. Known Limitations

The full list is in [KNOWN_LIMITATIONS.md](./KNOWN_LIMITATIONS.md). Key points:

| Area | Limitation |
|---|---|
| Payments | No payment gateway — billing module is tracking only |
| AI | Disabled by default — requires OpenRouter/OpenAI API key |
| Scale | Synchronous SQLAlchemy, 1 Uvicorn worker — suitable for ~50 concurrent users |
| Notifications | Telegram only — no email, no SMS |
| Onboarding | No self-service — agency admin creates each restaurant manually |
| Backups | Not automated — must be configured by the buyer |
| Tests | SQLite in-memory — some PostgreSQL edge cases not covered |

---

## 12. Support & Handover

### What is included in the sale

- Full source code (MIT License)
- This deployment guide
- Architecture documentation (`ARCHITECTURE.md`)
- API documentation (`API.md`)
- Security documentation (`SECURITY.md`)
- Known limitations (`KNOWN_LIMITATIONS.md`)
- Alembic migration history
- CI/CD pipeline (GitHub Actions)
- Docker configuration

### What is NOT included

- Production data or client list
- Render account or Neon account (buyer creates their own)
- Telegram bot tokens (buyer creates their own via BotFather)
- Sentry account
- Ongoing support (negotiate separately if needed)

### Recommended first steps after purchase

1. Fork the repository to your own GitHub account
2. Create Neon database and Render service
3. Set environment variables and deploy
4. Run `alembic upgrade head` and verify `/health`
5. Create your SuperAdmin account
6. Create your first Agency and Restaurant
7. Place a test order end-to-end
8. Set up daily database backups
9. Configure `ALLOWED_ORIGINS` with your domain
10. Set up Sentry for error monitoring

---

*Taomly v2.1.0 — White Label Multi-Tenant Restaurant SaaS Platform*
*Built with FastAPI · PostgreSQL · Telegram Mini App · PWA*
