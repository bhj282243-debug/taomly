# Deployment Guide

Complete guide for deploying the platform from scratch on Render + Neon.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + SQLAlchemy (sync) |
| Database | PostgreSQL (Neon recommended) |
| Hosting | Render Web Service |
| Storage | Cloudflare R2 (dish photos) |
| Bots | Telegram Bot API + Mini App |
| Frontend | Static HTML/PWA served by FastAPI |

---

## Step 1 — Create PostgreSQL Database

1. Sign up at [neon.tech](https://neon.tech) → **New Project**
2. Copy the connection string:
   ```
   postgresql://user:password@host/dbname?sslmode=require
   ```
3. Save it — you'll need it as `DATABASE_URL`

---

## Step 2 — Generate Secret Keys

Run each command once and save the results securely:

```bash
# SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# FERNET_KEY (encrypts Telegram bot tokens in DB — do not lose this)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# SUPERADMIN_PASSWORD_HASH (replace 'yourpassword')
python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('yourpassword'))"
```

---

## Step 3 — Deploy to Render

1. Fork or transfer the repository to your GitHub account
2. Go to [render.com](https://render.com) → **New → Web Service**
3. Connect your GitHub repository
4. Configure the service:

| Setting | Value |
|---|---|
| **Environment** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `alembic upgrade head && uvicorn api:app --host 0.0.0.0 --port $PORT --workers 1` |

5. Add environment variables (see `.env.example` for full list):

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | ✅ | Neon connection string with `sslmode=require` |
| `SECRET_KEY` | ✅ | Random 64-char string |
| `FERNET_KEY` | ✅ | Fernet key for bot token encryption |
| `SUPERADMIN_PASSWORD_HASH` | ✅ | bcrypt hash from Step 2 |
| `SUPERADMIN_EMAIL` | ✅ | Your superadmin login email |
| `WEBHOOK_URL` | ✅ | Your Render app URL — **base URL only**, no path suffix |
| `ENVIRONMENT` | ✅ | Set to `production` |
| `ALLOWED_ORIGINS` | ✅ in production | Comma-separated allowed origins |
| `BOT_TOKEN` | optional | Platform-level Telegram bot |
| `PLATFORM_NAME` | optional | Shown in PDF invoices |
| `PLATFORM_URL` | optional | Shown in PDF invoices |
| `PLATFORM_EMAIL` | optional | Shown in PDF invoices |
| `R2_ACCOUNT_ID` | optional | Required for dish photo upload |
| `R2_ACCESS_KEY_ID` | optional | Required for dish photo upload |
| `R2_SECRET_ACCESS_KEY` | optional | Required for dish photo upload |
| `R2_BUCKET_NAME` | optional | Default: `restaurant-photos` |

6. Click **Deploy**

### WEBHOOK_URL — important

`WEBHOOK_URL` must be the base URL of your deployment with **no path suffix**:

```
✅ Correct:  https://your-app.onrender.com
❌ Wrong:    https://your-app.onrender.com/webhook
❌ Wrong:    https://your-app.onrender.com/app
```

The platform automatically builds:
- `WEBHOOK_URL + /webhook` — platform bot Telegram webhook
- `WEBHOOK_URL + /webhook/{slug}` — per-restaurant Telegram webhook
- `WEBHOOK_URL + /app` — Mini App URL in Telegram buttons

---

## Step 4 — Verify Deployment

```
GET https://your-app.onrender.com/health
```

Expected:
```json
{"status": "healthy", "db": "ok"}
```

---

## Step 5 — Database Schema

Migrations run automatically via the Start Command (`alembic upgrade head`).

**Migration chain after a clean install:**

```
0001_initial          → 14 core tables
0002_add_badge_columns
0003_add_is_popular
0004_add_delivery_fields
0005_add_missing_tables → revoked_tokens, subscription_plans,
                          subscriptions, usage_events + seed data
```

**Verify:**
```bash
alembic current
# Expected: 0005 (head)
```

**All tables created by `alembic upgrade head`:**

```
agencies            restaurants         users
categories          products            restaurant_tables
orders              order_items         reservations
waiter_calls        revoked_tokens      subscription_plans
subscriptions       usage_events
```

### Existing database migration path

If you previously ran `MIGRATION_billing.sql` manually, the `subscription_plans`, `subscriptions`, and `usage_events` tables already exist. Running `alembic upgrade 0005` will fail with "table already exists".

**Safe path for existing databases:**

1. Verify the tables exist and match the expected schema:
   ```sql
   \d subscription_plans
   \d subscriptions
   \d usage_events
   ```
2. Check if `revoked_tokens` exists:
   ```sql
   \d revoked_tokens
   ```
3. If `revoked_tokens` does **not** exist, create it manually:
   ```sql
   CREATE TABLE revoked_tokens (
       id BIGSERIAL PRIMARY KEY,
       jti VARCHAR(36) NOT NULL,
       token_type VARCHAR(16) NOT NULL DEFAULT 'access',
       expires_at TIMESTAMPTZ NOT NULL,
       revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       CONSTRAINT uq_revoked_tokens_jti UNIQUE (jti)
   );
   CREATE INDEX ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);
   ```
4. Mark migration 0005 as applied **without running it**:
   ```bash
   python -m alembic stamp 0005
   ```
   ⚠️ Only use `alembic stamp` after you have manually verified that all tables in 0005 exist with the correct schema.

---

## Step 6 — First Login

Open `https://your-app.onrender.com/superadmin`

Log in with `SUPERADMIN_EMAIL` and the password you used to generate `SUPERADMIN_PASSWORD_HASH`.

---

## Step 7 — Create Agency and Restaurant

1. **Superadmin Console** → **Agencies** → **+ New Agency**
   - Fill: name, email, password
2. Log in at `/agency-admin` with agency credentials
3. **+ New Restaurant**:
   - Name, slug (URL identifier), admin password
   - Telegram Bot Token (from @BotFather)
   - Dispatcher ID (your Telegram user ID — for order notifications)

---

## Step 8 — Telegram Bot Setup

### Get a bot token

1. Open Telegram → `@BotFather` → `/newbot`
2. Choose name and username (must end in `bot`)
3. Copy the token: `1234567890:AAF...`

### Get your Telegram user ID

1. Telegram → `@userinfobot` → `/start`
2. Copy your numeric ID

### Connect to restaurant

1. Agency Admin → select restaurant → **Settings**
2. Paste Bot Token + Dispatcher ID → **Save**
3. The platform registers the webhook automatically

### Verify webhook

```bash
curl https://api.telegram.org/bot{TOKEN}/getWebhookInfo
```

Expected: `"url": "https://your-app.onrender.com/webhook/{slug}"`

### Platform bot (optional)

Set `BOT_TOKEN` in Render environment → redeploy.
The platform bot sends users a Mini App link on `/start`.

---

## Step 9 — Restaurant Tables (for dine-in orders)

Restaurant tables are created via the admin panel or API — no direct SQL required.

**Via admin panel (recommended):**
Open the restaurant admin panel → QR Codes tab → enter table count → click Generate.
Tables are saved to the database and QR images are available for download immediately.

**Via API:**
```
POST /api/restaurants/me/tables
Authorization: Bearer <restaurant_admin_token>
Content-Type: application/json

{"table_count": 10}
```

The QR codes in the admin panel will correctly resolve to the table IDs created above.

---

## Step 10 — Cloudflare R2 (Photo Storage)

Required for dish photo upload. Without it, photos return an error but the rest of the app works.

1. Cloudflare Dashboard → **R2** → **Create Bucket** (name it `restaurant-photos` or your custom name)
2. **Manage R2 API Tokens** → **Create API Token** → permissions: **Object Read & Write**
3. Copy Account ID, Access Key, Secret Key
4. Set in Render environment:
   ```
   R2_ACCOUNT_ID=...
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_BUCKET_NAME=restaurant-photos
   ```
5. Optional: configure a **Custom Domain** in R2 for public file URLs, then set `R2_PUBLIC_URL`

---

## Step 11 — Admin Panel URLs

After deployment, access the panels at:

| Panel | URL |
|---|---|
| Superadmin | `https://your-app.onrender.com/superadmin` |
| Agency Admin | `https://your-app.onrender.com/agency-admin` |
| Restaurant Admin | `https://your-app.onrender.com/admin?slug=restaurant-slug` |
| Customer Mini App | `https://your-app.onrender.com/app?slug=restaurant-slug` |

---

## Clean Install Verification Checklist

```
[ ] GET /health → {"status": "healthy", "db": "ok"}
[ ] alembic current → 0005 (head)
[ ] All 14 tables exist in database
[ ] Superadmin login works
[ ] Superadmin logout works (JWT revocation — requires revoked_tokens table)
[ ] Agency created
[ ] Restaurant created
[ ] Menu item created
[ ] Telegram bot webhook registered
[ ] Mini App opens in Telegram
[ ] Test order placed
[ ] Order appears in admin panel
[ ] Telegram notification received on new order
[ ] Order status change triggers client notification
[ ] Reservation works
[ ] Waiter call works
[ ] Billing endpoint: GET /api/billing/subscription → 200
[ ] Invoice endpoint: GET /api/billing/invoice → PDF generated
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/health` returns unhealthy | Bad DATABASE_URL | Check Neon connection string, `sslmode=require` |
| `alembic upgrade head` fails | DB not reachable | Set DATABASE_URL env var before running |
| Logout returns 500 | `revoked_tokens` table missing | Run migration 0005 or create manually (see Step 5) |
| Mini App button URL is `/app` | WEBHOOK_URL not set | Set `WEBHOOK_URL=https://your-app.onrender.com` |
| Telegram webhook not registering | Wrong WEBHOOK_URL | Must be HTTPS base URL, no path suffix |
| Photos not uploading | R2 not configured | Set R2_* env vars (Step 10) |
| CORS error in browser | ALLOWED_ORIGINS missing | Set `ALLOWED_ORIGINS=https://your-domain.com` |
| Billing returns 500 | Billing tables missing | Check migration 0005 applied |
