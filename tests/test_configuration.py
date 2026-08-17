"""
tests/test_configuration.py — Task 6: Production Configuration & Environment Hardening

Проверяет:
  1. SUPERADMIN_EMAIL отсутствует → RuntimeError при загрузке config
  2. WEBHOOK_SECRET отсутствует в production → RuntimeError при загрузке config
  3. WEBHOOK_SECRET явно задан → принимается без ошибок
  4. В development без WEBHOOK_SECRET используется HMAC-fallback (не sha256)
  5. Старый sha256-derived fallback больше не используется
  6. Webhook authentication не регрессировал (hmac.compare_digest работает корректно)
"""

import hashlib
import hmac
import os
from unittest.mock import patch

import pytest


# ─────────────────────────────────────────────────────────────────
# Вспомогательная функция: изолированная загрузка _load_webhook_secret
# напрямую (без перезапуска всего config-модуля, чтобы не конфликтовать
# с уже загруженными settings).
# ─────────────────────────────────────────────────────────────────
def _call_load_webhook_secret(secret_key: str, webhook_secret_env: str, environment: str) -> str:
    """
    Вызывает _load_webhook_secret() в изолированном окружении.
    Подменяет os.getenv только внутри функции через patch.
    """
    # Импортируем функцию напрямую из модуля config
    from config import _load_webhook_secret

    env_map = {
        "WEBHOOK_SECRET": webhook_secret_env,
        "ENVIRONMENT": environment,
    }

    original_getenv = os.getenv

    def patched_getenv(name, default=None):
        if name in env_map:
            val = env_map[name]
            return val if val is not None else default
        return original_getenv(name, default)

    with patch("os.getenv", side_effect=patched_getenv):
        return _load_webhook_secret(secret_key)


# ─────────────────────────────────────────────────────────────────
# 1. SUPERADMIN_EMAIL отсутствует → config failure
# ─────────────────────────────────────────────────────────────────
def test_superadmin_email_missing_raises():
    """
    _require("SUPERADMIN_EMAIL") должен бросать RuntimeError
    если переменная не задана.

    Тестируем _require() напрямую — не перезагружаем весь config,
    чтобы не конфликтовать с уже инициализированными settings.
    """
    from config import _require

    with patch.dict(os.environ, {}, clear=False):
        # Убираем SUPERADMIN_EMAIL если он есть
        env_backup = os.environ.pop("SUPERADMIN_EMAIL", None)
        try:
            with pytest.raises(RuntimeError, match="SUPERADMIN_EMAIL"):
                _require("SUPERADMIN_EMAIL")
        finally:
            if env_backup is not None:
                os.environ["SUPERADMIN_EMAIL"] = env_backup


def test_superadmin_email_present_returns_value():
    """_require("SUPERADMIN_EMAIL") возвращает значение если переменная задана."""
    from config import _require

    with patch.dict(os.environ, {"SUPERADMIN_EMAIL": "test-admin@example.com"}):
        result = _require("SUPERADMIN_EMAIL")
    assert result == "test-admin@example.com"


# ─────────────────────────────────────────────────────────────────
# 2. WEBHOOK_SECRET отсутствует в production → config failure
# ─────────────────────────────────────────────────────────────────
def test_webhook_secret_missing_in_production_raises():
    """
    Если ENVIRONMENT=production и WEBHOOK_SECRET не задан — RuntimeError.
    """
    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        _call_load_webhook_secret(
            secret_key="any-secret-key-value",
            webhook_secret_env="",
            environment="production",
        )


def test_webhook_secret_error_message_contains_generation_hint():
    """RuntimeError содержит подсказку как сгенерировать WEBHOOK_SECRET."""
    with pytest.raises(RuntimeError, match="secrets.token_hex"):
        _call_load_webhook_secret(
            secret_key="any-secret-key-value",
            webhook_secret_env="",
            environment="production",
        )


# ─────────────────────────────────────────────────────────────────
# 3. WEBHOOK_SECRET явно задан → принимается в любом окружении
# ─────────────────────────────────────────────────────────────────
def test_webhook_secret_explicit_accepted_in_production():
    """Явно заданный WEBHOOK_SECRET принимается в production."""
    result = _call_load_webhook_secret(
        secret_key="any-secret-key-value",
        webhook_secret_env="my-explicit-webhook-secret-value",
        environment="production",
    )
    assert result == "my-explicit-webhook-secret-value"


def test_webhook_secret_explicit_accepted_in_development():
    """Явно заданный WEBHOOK_SECRET принимается в development."""
    result = _call_load_webhook_secret(
        secret_key="any-secret-key-value",
        webhook_secret_env="my-explicit-webhook-secret-value",
        environment="development",
    )
    assert result == "my-explicit-webhook-secret-value"


# ─────────────────────────────────────────────────────────────────
# 4. Development без WEBHOOK_SECRET: HMAC-fallback, не sha256
# ─────────────────────────────────────────────────────────────────
def test_webhook_secret_dev_fallback_is_hmac_not_sha256():
    """
    В development без WEBHOOK_SECRET используется HMAC(SECRET_KEY, "webhook-secret"),
    а НЕ sha256(SECRET_KEY)[:64].
    """
    test_key = "test-secret-key-minimum-32-chars-here-ok"

    result = _call_load_webhook_secret(
        secret_key=test_key,
        webhook_secret_env="",
        environment="development",
    )

    expected_hmac = hmac.new(test_key.encode(), b"webhook-secret", "sha256").hexdigest()
    old_sha256 = hashlib.sha256(test_key.encode()).hexdigest()[:64]

    assert result == expected_hmac, "Dev fallback должен быть HMAC(SECRET_KEY, 'webhook-secret')"
    assert result != old_sha256, "Dev fallback НЕ должен быть sha256(SECRET_KEY)[:64]"


def test_webhook_secret_dev_fallback_not_used_in_production():
    """
    В production HMAC-fallback недоступен — RuntimeError даже при наличии SECRET_KEY.
    """
    with pytest.raises(RuntimeError):
        _call_load_webhook_secret(
            secret_key="some-key-that-could-generate-fallback",
            webhook_secret_env="",
            environment="production",
        )


# ─────────────────────────────────────────────────────────────────
# 5. Старый sha256-derived fallback больше не используется
# ─────────────────────────────────────────────────────────────────
def test_old_sha256_fallback_not_used():
    """
    Регрессионный тест: старый sha256(SECRET_KEY)[:64] fallback удалён.
    Dev fallback отличается от него по значению.
    """
    test_key = "test-secret-key-minimum-32-chars-here-ok"
    old_fallback = hashlib.sha256(test_key.encode()).hexdigest()[:64]

    dev_fallback = _call_load_webhook_secret(
        secret_key=test_key,
        webhook_secret_env="",
        environment="development",
    )

    assert dev_fallback != old_fallback, (
        "Dev fallback не должен совпадать со старым sha256 derivation. "
        f"old={old_fallback!r}, got={dev_fallback!r}"
    )


# ─────────────────────────────────────────────────────────────────
# 6. Webhook authentication не регрессировал
# ─────────────────────────────────────────────────────────────────
def test_webhook_auth_correct_secret_accepted(db, restaurant, client):
    """
    POST /webhook/{slug} с правильным WEBHOOK_SECRET → не 403.

    Используем settings.WEBHOOK_SECRET (то что реально загружено в текущем
    тестовом процессе) — это гарантирует что authentication работает с
    фактически загруженным значением независимо от того, explicit или fallback.
    """
    from config import settings
    from fastapi.testclient import TestClient
    from api import app

    app.dependency_overrides[__import__("database").get_db] = lambda: db

    c = TestClient(app, raise_server_exceptions=False)
    try:
        resp = c.post(
            f"/webhook/{restaurant.slug}",
            json={"update_id": 999},
            headers={"X-Telegram-Bot-Api-Secret-Token": settings.WEBHOOK_SECRET},
        )
        # Не 403 — секрет принят. Может быть 200 (ok/not ok) в зависимости от бота.
        assert resp.status_code != 403, (
            f"Правильный WEBHOOK_SECRET должен быть принят, получили 403. "
            f"Response: {resp.text}"
        )
    finally:
        app.dependency_overrides.clear()


def test_webhook_auth_wrong_secret_rejected(db, restaurant):
    """
    POST /webhook/{slug} с неправильным секретом → 403.
    """
    from fastapi.testclient import TestClient
    from api import app

    app.dependency_overrides[__import__("database").get_db] = lambda: db

    c = TestClient(app, raise_server_exceptions=False)
    try:
        resp = c.post(
            f"/webhook/{restaurant.slug}",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "definitely-wrong-secret"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_webhook_auth_missing_secret_rejected(db, restaurant):
    """
    POST /webhook/{slug} без заголовка X-Telegram-Bot-Api-Secret-Token → 403.
    """
    from fastapi.testclient import TestClient
    from api import app

    app.dependency_overrides[__import__("database").get_db] = lambda: db

    c = TestClient(app, raise_server_exceptions=False)
    try:
        resp = c.post(
            f"/webhook/{restaurant.slug}",
            json={"update_id": 1},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()
