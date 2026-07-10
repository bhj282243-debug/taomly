# Taomly Deployment Guide

## Stack

- Backend: FastAPI + SQLAlchemy (sync)
- Database: Neon PostgreSQL
- Hosting: Render (Web Service)
- Telegram: Bot API + Mini App
- PWA: Static files on Render

## Environment Variables

DATABASE_URL — Neon PostgreSQL connection string
SECRET_KEY — JWT signing key (min 32 chars)
FERNET_KEY — Fernet encryption key (base64)
AI_ENABLED — true / false (default: false)
AI_PROVIDER — openai / anthropic / openrouter / gemini
AI_API_KEY — API key for AI provider
AI_MODEL — Model name (e.g. gpt-4o-mini)
SENTRY_DSN — Sentry error tracking (optional)

## Generate Keys

SECRET_KEY: openssl rand -hex 32

FERNET_KEY: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

## Render Setup

1. Connect GitHub repo: bhj282243-debug/taomly
2. Runtime: Python 3.11
3. Build command: pip install -r requirements.txt
4. Start command: uvicorn api:app --host 0.0.0.0 --port $PORT
5. Add all environment variables
6. Deploy

## Database Migration

Run: alembic upgrade head

Migrations:
- 0001_initial.py — all base tables
- 0002_add_badge_columns.py — badge columns for products

## Telegram Webhook

Webhooks register automatically when creating a restaurant via Agency Admin.

Manual: POST https://api.telegram.org/bot{TOKEN}/setWebhook
Body: url = https://taomly.onrender.com/webhook/{slug}

## Admin Panels

Agency Admin: https://taomly.onrender.com/static/agency_admin.html
Restaurant Admin: https://taomly.onrender.com/static/admin.html
Client Mini App: https://taomly.onrender.com/static/index.html

## Default Credentials

Agency: admin@taomly.uz / 12345678
Restaurant 1: chinar / 12345678
Restaurant 2: taomly-test-2 / secret

## Tests

Run: pytest tests/ -v
