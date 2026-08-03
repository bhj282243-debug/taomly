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


# ──────────────────────────────────────────
# ВАЛИДАЦИЯ ВХОДНЫХ ДАННЫХ (max_length, min_items, max_items)
# ──────────────────────────────────────────

class TestAIValidation:
    """
    Проверяет что Pydantic отклоняет запросы с превышением лимитов полей.
    Ожидаемый статус: 422 Unprocessable Entity.
    """

    def test_dish_name_too_long(self, client, restaurant_token):
        """dish_name > 200 символов → 422."""
        response = client.post(
            "/api/ai/generate-description",
            json={"dish_name": "А" * 201, "language": "ru"},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_ingredients_too_long(self, client, restaurant_token):
        """ingredients > 1000 символов → 422."""
        response = client.post(
            "/api/ai/generate-description",
            json={"dish_name": "Плов", "ingredients": "х" * 1001},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_dish_name_empty(self, client, restaurant_token):
        """dish_name пустая строка → 422 (min_length=1)."""
        response = client.post(
            "/api/ai/generate-description",
            json={"dish_name": ""},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_translate_menu_empty_list(self, client, restaurant_token):
        """items=[] → 422 (min_length=1)."""
        response = client.post(
            "/api/ai/translate-menu",
            json={"items": [], "target_language": "uz"},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_translate_menu_too_many_items(self, client, restaurant_token):
        """items > 50 элементов → 422 (max_length=50)."""
        items = [{"name": f"Блюдо {i}"} for i in range(51)]
        response = client.post(
            "/api/ai/translate-menu",
            json={"items": items, "target_language": "uz"},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_translate_menu_item_name_too_long(self, client, restaurant_token):
        """Имя позиции в items > 200 символов → 422."""
        response = client.post(
            "/api/ai/translate-menu",
            json={"items": [{"name": "Б" * 201}], "target_language": "uz"},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_suggest_tags_description_too_long(self, client, restaurant_token):
        """description > 1000 символов → 422."""
        response = client.post(
            "/api/ai/suggest-tags",
            json={"dish_name": "Лагман", "description": "д" * 1001},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_menu_seo_summary_too_long(self, client, restaurant_token):
        """menu_summary > 1000 символов → 422."""
        response = client.post(
            "/api/ai/generate-seo",
            json={"restaurant_name": "Чинор", "menu_summary": "с" * 1001},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_restaurant_name_empty(self, client, restaurant_token):
        """restaurant_name пустая строка → 422 (min_length=1)."""
        response = client.post(
            "/api/ai/generate-seo",
            json={"restaurant_name": ""},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 422

    def test_valid_request_passes(self, client, restaurant_token):
        """Корректный запрос проходит валидацию и возвращает 200."""
        response = client.post(
            "/api/ai/generate-description",
            json={"dish_name": "Плов", "ingredients": "рис, мясо, морковь", "language": "ru"},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 200

    def test_translate_menu_valid_items(self, client, restaurant_token):
        """Список из 1 корректного элемента проходит валидацию."""
        response = client.post(
            "/api/ai/translate-menu",
            json={"items": [{"name": "Плов", "description": "Вкусный", "price": 45000}], "target_language": "uz"},
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert response.status_code == 200


# ──────────────────────────────────────────
# RATE LIMIT
# ──────────────────────────────────────────

class TestAIRateLimit:
    """
    Проверяет что rate limit 10/minute срабатывает на 11-м запросе.
    slowapi в тестовой среде использует in-memory хранилище,
    поэтому лимит сбрасывается между тестами при разных клиентах.
    """

    def test_rate_limit_exceeded(self, client, restaurant_token):
        """11-й запрос подряд → 429 Too Many Requests."""
        headers = {"Authorization": f"Bearer {restaurant_token}"}
        payload = {"dish_name": "Плов", "language": "ru"}

        # Первые 10 — должны пройти
        for i in range(10):
            r = client.post("/api/ai/generate-description", json=payload, headers=headers)
            assert r.status_code == 200, f"Запрос {i+1} ожидал 200, получил {r.status_code}"

        # 11-й — должен быть отклонён
        r = client.post("/api/ai/generate-description", json=payload, headers=headers)
        assert r.status_code == 429, f"Ожидали 429, получили {r.status_code}"
