"""
config.py — Taomly Platform
Единый источник конфигурации. Все env-переменные читаются здесь.
Импортировать в других модулях: from config import settings

Зачем:
  - Устраняет дублирование логики WEBHOOK_SECRET в api.py и agency.py
  - Все переменные проверяются при старте — не в рантайме
  - Единое место для документирования всех env-переменных
"""

import hashlib
import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    """Возвращает env-переменную или бросает RuntimeError при старте."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is required but not set. "
            f"See README.md → Environment Variables."
        )
    return value


class _Settings:
    # ── Обязательные ──────────────────────────────────────────────────
    DATABASE_URL: str = _require("DATABASE_URL")
    SECRET_KEY: str = _require("SECRET_KEY")
    FERNET_KEY: str = _require("FERNET_KEY")

    # ── Опциональные с дефолтами ───────────────────────────────────────
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")

    # JWT
    ACCESS_TOKEN_EXPIRE_HOURS: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "24"))

    # Telegram initData max age (seconds). Default 24h, set 3600 for higher security.
    MAX_INIT_DATA_AGE_SECONDS: int = int(os.getenv("MAX_INIT_DATA_AGE_SECONDS", "86400"))

    # WEBHOOK_SECRET: либо задан явно, либо деривируется из SECRET_KEY.
    # Telegram принимает только [A-Za-z0-9_-], длина 1–256.
    # sha256(SECRET_KEY)[:64] всегда в этом диапазоне.
    WEBHOOK_SECRET: str = os.getenv(
        "WEBHOOK_SECRET",
        hashlib.sha256(SECRET_KEY.encode()).hexdigest()[:64],
    )

    # CORS: comma-separated list of allowed origins.
    # Example: "https://taomly.uz,https://taomly.onrender.com"
    # Empty string → allows all origins (development only).
    ALLOWED_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
        if o.strip()
    ]

    # Rate limiting (requests per minute for sensitive endpoints)
    RATE_LIMIT_LOGIN: str = os.getenv("RATE_LIMIT_LOGIN", "10/minute")
    RATE_LIMIT_API: str = os.getenv("RATE_LIMIT_API", "120/minute")


settings = _Settings()
