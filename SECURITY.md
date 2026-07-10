# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please email: admin@taomly.uz

Do not open a public GitHub issue for security problems.

We will respond within 48 hours.

---

## Authentication

- Agency admin: JWT token (30 days expiry)
- Restaurant admin: JWT token (30 days expiry)
- Telegram users: HMAC-SHA256 signature verification per restaurant

---

## Data Protection

- Telegram bot tokens encrypted with Fernet (AES-128-CBC)
- Passwords hashed with bcrypt (cost factor 12)
- All API endpoints protected against IDOR attacks
- Each restaurant sees only its own data (filtered by restaurant_id)

---

## Rate Limiting

- Agency registration: 5 requests / 10 minutes
- Order creation: 60 requests / minute
- General API: 100 requests / minute

---

## Infrastructure

- HTTPS only (Render TLS)
- Security headers: X-Content-Type-Options, X-Frame-Options, HSTS
- Database: Neon PostgreSQL (isolated, encrypted at rest)
- Secrets stored as environment variables (never in code)

---

## Environment Variables Required

- SECRET_KEY — JWT signing key (min 32 chars)
- FERNET_KEY — Fernet encryption key (must be valid base64)
- DATABASE_URL — Neon PostgreSQL connection string

---

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |
