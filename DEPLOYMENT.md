# DEPLOYMENT.md — Taomly Platform

## Quick Reference

| Environment | URL | Branch |
|-------------|-----|--------|
| Production | https://taomly.onrender.com | `main` |
| Local Dev | http://localhost:8000 | any |

---

## Local Development

### Prerequisites
- Python 3.11+
- PostgreSQL 15+ (or Docker)
- Git

### Option A: Direct Python

```bash
# 1. Clone
git clone https://github.com/bhj282243-debug/taomly.git
cd taomly

# 2. Virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
pip install pytest pytest-cov ruff pre-commit  # dev tools

# 4. Environment
cp .env.example .env
# Edit .env — fill in DATABASE_URL, SECRET_KEY, FERNET_KEY

# 5. Database migrations
alembic upgrade head

# 6. Run
uvicorn api:app --reload
```

### Option B: Docker Compose

```bash
# 1. Clone and setup env
git clone https://github.com/bhj282243-debug/taomly.git
cd taomly
cp .env.example .env
# Edit .env — only BOT_TOKEN, SENTRY_DSN needed (DB is local postgres)

# 2. Start everything
docker compose up --build

# 3. Run migrations (first time)
docker compose --profile migrate up migrate

# App running at: http://localhost:8000
```

### Dev Tools Setup

```bash
# Install pre-commit hooks (runs ruff on every commit)
pre-commit install

# Run linter manually
ruff check .
ruff format .

# Run tests
pytest

# Run tests with coverage
pytest --cov=. --cov-report=html
open htmlcov/index.html
```

---

## Production Deployment (Render)

### Initial Setup

1. **Create Render Web Service**
   - Connect GitHub repo
   - Runtime: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn api:app --host 0.0.0.0 --port $PORT`

2. **Environment Variables** (Render Dashboard → Environment)

   | Variable | Value |
   |----------|-------|
   | `DATABASE_URL` | Neon connection string |
   | `SECRET_KEY` | `openssl rand -hex 32` |
   | `FERNET_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
   | `BOT_TOKEN` | Platform bot token from @BotFather |
   | `WEBHOOK_URL` | `https://taomly.onrender.com` |
   | `SENTRY_DSN` | From sentry.io project settings |
   | `ALLOWED_ORIGINS` | `https://taomly.onrender.com,https://taomly.uz` |

3. **Database Migrations**
   - Open Neon SQL Editor
   - Run `MIGRATION_badges.sql` (if upgrading existing DB)
   - OR: for fresh install, Alembic handles everything via `create_all()`

### Deploy Process

```bash
# Standard deploy: push to main
git push origin main

# Render auto-deploys on push to main branch
# Monitor at: https://dashboard.render.com
```

### Health Check

```bash
# Check if app is healthy
curl https://taomly.onrender.com/health

# Expected response:
# {"status": "healthy", "db": "ok"}
```

---

## Database Management

### Running Alembic Migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Generate new migration (after model changes)
alembic revision --autogenerate -m "add_feature_x"

# For existing DB created with create_all():
# Mark current state as matching initial migration
alembic stamp 0001_initial
```

### Neon SQL Editor (Production)

For schema changes that can't use Alembic (emergency patches):
```sql
-- Always use IF NOT EXISTS / IF EXISTS for safety
ALTER TABLE products ADD COLUMN IF NOT EXISTS new_column BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS ix_products_new ON products (restaurant_id, new_column);
```

---

## Monitoring

### Sentry

- Dashboard: https://sentry.io (your project)
- Errors auto-reported with stack traces
- Performance traces: 10% of requests sampled

### Render Logs

```bash
# View live logs in Render dashboard
# Or via Render CLI:
render logs --service taomly
```

### Health Check Endpoint

`GET /health` — returns 200 if DB is reachable, 503 if not.
Render uses this to detect crashed instances.

---

## Backup Policy

### Neon Automatic Backups
- Free tier: 7-day Point-in-Time Recovery
- Pro tier: 30-day PITR + instant restore

### Manual Backup (recommended before major deploys)

```bash
# Export full database
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore
psql $DATABASE_URL < backup_YYYYMMDD_HHMMSS.sql
```

### Before Every Production Migration

```bash
# 1. Backup
pg_dump $DATABASE_URL > pre_migration_$(date +%Y%m%d).sql

# 2. Apply migration
alembic upgrade head

# 3. Verify
curl https://taomly.onrender.com/health
```

---

## Incident Response

### App is down (503)

```bash
# 1. Check health
curl https://taomly.onrender.com/health

# 2. Check Render logs
# dashboard.render.com → taomly → Logs

# 3. Check Sentry for recent errors
# sentry.io → taomly → Issues

# 4. If DB issue — check Neon status
# neon.tech → dashboard → your project
```

### Rollback

```bash
# Render: deploy previous commit
# Go to: dashboard.render.com → taomly → Deploys → select previous → Rollback

# Database rollback (if migration caused issues)
alembic downgrade -1
```

---

## CI/CD Pipeline

```
git push origin main
        ↓
GitHub Actions CI
  ├── lint (ruff)
  ├── test (pytest, SQLite in-memory)
  └── docker build
        ↓ (all green)
Render auto-deploy
        ↓
Health check: GET /health → 200
```

### Test locally before push

```bash
# Exact same checks as CI
ruff check .
ruff format --check .
pytest tests/ -v
```
