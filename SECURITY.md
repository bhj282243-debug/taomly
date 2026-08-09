# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please email the platform owner directly.
Set your security contact in `SECURITY.md` after purchase.

Do not open a public GitHub issue for security problems.

We will respond within 48 hours.

---

## Authentication

- Agency admin: JWT token (8 hours expiry, configurable via ACCESS_TOKEN_EXPIRE_HOURS)
- Restaurant admin: JWT token (8 hours expiry, configurable via ACCESS_TOKEN_EXPIRE_HOURS)
- Superadmin: JWT token (12 hours expiry, hardcoded)
- Telegram users: HMAC-SHA256 signature verification per restaurant

---

## Data Protection

- Telegram bot tokens encrypted with Fernet (AES-128-CBC)
- Passwords hashed with bcrypt (cost factor 12)
- All API endpoints protected against IDOR attacks
- Each restaurant sees only its own data (filtered by restaurant_id)

---

## Rate Limiting

- Agency registration: 5 requests / hour
- Order creation: 10 requests / minute
- Order status fetch: 30 requests / minute
- AI endpoints: 10 requests / minute
- General API default: not set (planned, configurable via RATE_LIMIT_API env var)

---

## Infrastructure

- HTTPS only (Render TLS)
- Security headers: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Content-Security-Policy
- HSTS: planned (add Strict-Transport-Security header before production)
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
| 2.1.x   | Yes       |
| 1.x     | No        |
