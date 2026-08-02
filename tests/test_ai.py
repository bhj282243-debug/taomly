"""
tests/test_ai.py — AI Router тесты (Stage 3, Sprint 3.1)

Проверяет:
  - Все 4 AI endpoints возвращают feature_not_available при AI_ENABLED=false
  - Без авторизации → 401/403
  - Структура ответа корректна
"""

import pytest
from fastapi.testclient import TestClient


# ──────────────────────────────────────────
# AI DISABLED (default) — все endpoints
# ──────────────────────────────────────────

def test_generate_description_ai_disabled(client: TestClient, restaurant_token: str):
    """При AI_ENABLED=false возвращает feature_not_available."""
    response = client.post(
        "/api/ai/generate-description",
        json={"dish_name": "Плов", "language": "ru"},
        headers={"Authorization": f"Bearer {restaurant_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "feature_not_available"
    assert data["ai_enabled"] is False


def test_translate_menu_ai_disabled(client: TestClient, restaurant_token: str):
    """При AI_ENABLED=false возвращает feature_not_available."""
    response = client.post(
        "/api/ai/translate-menu",
        json={"items": [{"name": "Плов", "price": 45000}], "target_language": "uz"},
        headers={"Authorization": f"Bearer {restaurant_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "feature_not_available"
    assert data["ai_enabled"] is False


def test_suggest_tags_ai_disabled(client: TestClient, restaurant_token: str):
    """При AI_ENABLED=false возвращает feature_not_available."""
    response = client.post(
        "/api/ai/suggest-tags",
        json={"dish_name": "Лагман", "description": "Острый суп"},
        headers={"Authorization": f"Bearer {restaurant_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "feature_not_available"
    assert data["ai_enabled"] is False


def test_generate_seo_ai_disabled(client: TestClient, restaurant_token: str):
    """При AI_ENABLED=false возвращает feature_not_available."""
    response = client.post(
        "/api/ai/generate-seo",
        json={"restaurant_name": "Чинор", "language": "en"},
        headers={"Authorization": f"Bearer {restaurant_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "feature_not_available"
    assert data["ai_enabled"] is False


# ──────────────────────────────────────────
# БЕЗ АВТОРИЗАЦИИ
# Используем отдельный "сырой" клиент без auth-override,
# иначе dependency_overrides из fixture client маскирует 401/403.
# ──────────────────────────────────────────

def _raw_client(db):
    """TestClient без auth-override — только get_db замокирован."""
    from api import app
    from auth import get_db
    app.dependency_overrides = {get_db: lambda: db}
    client = TestClient(app, raise_server_exceptions=False)
    return client


def test_generate_description_no_auth(db):
    """Без токена → 401/403."""
    raw = _raw_client(db)
    response = raw.post(
        "/api/ai/generate-description",
        json={"dish_name": "Плов"},
    )
    app.dependency_overrides.clear()
    assert response.status_code in (401, 403)


def test_translate_menu_no_auth(db):
    """Без токена → 401/403."""
    raw = _raw_client(db)
    response = raw.post(
        "/api/ai/translate-menu",
        json={"items": [], "target_language": "uz"},
    )
    app.dependency_overrides.clear()
    assert response.status_code in (401, 403)


def test_suggest_tags_no_auth(db):
    """Без токена → 401/403."""
    raw = _raw_client(db)
    response = raw.post(
        "/api/ai/suggest-tags",
        json={"dish_name": "Лагман"},
    )
    app.dependency_overrides.clear()
    assert response.status_code in (401, 403)


def test_generate_seo_no_auth(db):
    """Без токена → 401/403."""
    raw = _raw_client(db)
    response = raw.post(
        "/api/ai/generate-seo",
        json={"restaurant_name": "Чинор"},
    )
    app.dependency_overrides.clear()
    assert response.status_code in (401, 403)
