"""
config.py — Taomly Platform
Единый источник конфигурации. Все env-переменные читаются здесь.
Импортировать в других модулях: from config import settings

Изменения v7 (Security Patch C-1):
  - SUPERADMIN_PASSWORD хранится как bcrypt-хэш в env (SUPERADMIN_PASSWORD_HASH).
    Для обратной совместимости: если задан SUPERADMIN_PASSWORD (plain) —
    принимается при старте, но логируется предупреждение о необходимости мигрировать.
    Рекомендуется: сгенерировать хэш и переложить в SUPERADMIN_PASSWORD_HASH.

    Генерация хэша (выполнить один раз):
      python -c "from passlib.context import CryptContext; \
        ctx=CryptContext(schemes=['bcrypt'],deprecated='auto'); \
        print(ctx.hash('ваш_пароль'))"

    Затем в .env / Render env:
      SUPERADMIN_PASSWORD_HASH=<результат выше>
      # SUPERADMIN_PASSWORD= (убрать или оставить пустым)
"""

import hashlib
import logging
import os

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _require(name: str) -> str:
    """Возвращает env-переменную или бросает RuntimeError при старте."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is required but not set. "
            f"See README.md → Environment Variables."
        )
    return value


def _validate_fernet_key(key: str) -> str:
    """Проверяет что FERNET_KEY валиден — при старте, не в рантайме."""
    try:
        Fernet(key.encode())
    except Exception:
        raise RuntimeError(
            "FERNET_KEY невалиден. Сгенерируйте корректный ключ:\n"
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return key


def _load_superadmin_password_hash() -> str:
    """
    Загружает bcrypt-хэш пароля суперадмина.

    Порядок поиска:
      1. SUPERADMIN_PASSWORD_HASH — предпочтительно (bcrypt-хэш).
      2. SUPERADMIN_PASSWORD — legacy plaintext. Принимается для обратной
         совместимости, но при старте логируется WARNING с инструкцией миграции.

    Возвращает строку, которую superadmin.py сравнивает через pwd_context.verify().
    Если задан plaintext — хэшируется прямо здесь (in-memory, не сохраняется).
    """
    hash_val = os.getenv("SUPERADMIN_PASSWORD_HASH", "").strip()
    if hash_val:
        if not hash_val.startswith("$2b$") and not hash_val.startswith("$2a$"):
            raise RuntimeError(
                "SUPERADMIN_PASSWORD_HASH должен быть bcrypt-хэшем "
                "(начинается с $2b$ или $2a$). "
                "Сгенерируйте: python -c \"from passlib.context import CryptContext; "
                "ctx=CryptContext(schemes=['bcrypt'],deprecated='auto'); "
                "print(ctx.hash('ваш_пароль'))\""
            )
        return hash_val

    plain = os.getenv("SUPERADMIN_PASSWORD", "").strip()
    if plain:
        from passlib.context import CryptContext
        _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        hashed = _ctx.hash(plain)
        import sys
        print(
            "\n[TAOMLY SECURITY WARNING] Используется SUPERADMIN_PASSWORD (plaintext).\n"
            "Мигрируйте на SUPERADMIN_PASSWORD_HASH:\n"
            f"  1. Скопируйте хэш: {hashed}\n"
            "  2. В Render/env: SUPERADMIN_PASSWORD_HASH=<хэш>\n"
            "  3. Удалите SUPERADMIN_PASSWORD из env.\n",
            file=sys.stderr,
        )
        return hashed

    raise RuntimeError(
        "Не задан SUPERADMIN_PASSWORD_HASH (рекомендуется) или SUPERADMIN_PASSWORD. "
        "Установите одну из переменных. "
        "See README.md → Environment Variables."
    )


class _Settings:
    DATABASE_URL: str = _require("DATABASE_URL")
    SECRET_KEY: str = _require("SECRET_KEY")
    FERNET_KEY: str = _validate_fernet_key(_require("FERNET_KEY"))

    SUPERADMIN_PASSWORD_HASH: str = _load_superadmin_password_hash()
    SUPERADMIN_EMAIL: str = os.getenv("SUPERADMIN_EMAIL", "superadmin@taomly.uz")

    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")

    # JWT: 8 часов — баланс UX и безопасности.
    ACCESS_TOKEN_EXPIRE_HOURS: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "8"))

    MAX_INIT_DATA_AGE_SECONDS: int = int(os.getenv("MAX_INIT_DATA_AGE_SECONDS", "3600"))

    WEBHOOK_SECRET: str = os.getenv(
        "WEBHOOK_SECRET",
        hashlib.sha256(SECRET_KEY.encode()).hexdigest()[:64],
    )

    ALLOWED_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
        if o.strip()
    ]

    RATE_LIMIT_LOGIN: str = os.getenv("RATE_LIMIT_LOGIN", "10/minute")
    RATE_LIMIT_API: str = os.getenv("RATE_LIMIT_API", "120/minute")
    RATE_LIMIT_SUPERADMIN_LOGIN: str = os.getenv("RATE_LIMIT_SUPERADMIN_LOGIN", "5/minute")


settings = _Settings()
