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
# ──────────────────────────────────────────

def test_generate_description_no_auth(client: TestClient):
    """Без токена → 401/403."""
    response = client.post(
        "/api/ai/generate-description",
        json={"dish_name": "Плов"},
    )
    assert response.status_code in (401, 403)


def test_translate_menu_no_auth(client: TestClient):
    """Без токена → 401/403."""
    response = client.post(
        "/api/ai/translate-menu",
        json={"items": [], "target_language": "uz"},
    )
    assert response.status_code in (401, 403)


def test_suggest_tags_no_auth(client: TestClient):
    """Без токена → 401/403."""
    response = client.post(
        "/api/ai/suggest-tags",
        json={"dish_name": "Лагман"},
    )
    assert response.status_code in (401, 403)


def test_generate_seo_no_auth(client: TestClient):
    """Без токена → 401/403."""
    response = client.post(
        "/api/ai/generate-seo",
        json={"restaurant_name": "Чинор"},
    )
    assert response.status_code in (401, 403)
