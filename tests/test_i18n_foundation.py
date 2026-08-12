"""
tests/test_i18n_foundation.py — Task 1B.2

Foundation tests для Restaurant.language.

Проверяют:
  1. Новый ресторан по умолчанию имеет language="uz"
  2. GET public restaurant возвращает language
  3. PATCH language="ru" работает
  4. PATCH language="en" работает
  5. PATCH language="de" отклоняется (400)
  6. PATCH language="abc" отклоняется (400)
  7. PATCH language="" отклоняется (400)
  8. Currency продолжает работать после добавления language

SQLite: все тесты запускаются без PostgreSQL.
"""

import pytest


# ──────────────────────────────────────────
# TEST 1: Новый Restaurant по умолчанию получает language="uz"
# ──────────────────────────────────────────
@pytest.mark.integration
def test_restaurant_language_default(restaurant):
    """
    Модель Restaurant должна иметь language="uz" по умолчанию.
    Проверяет поле модели напрямую — не зависит от API.
    """
    lang = getattr(restaurant, "language", None)
    assert lang is not None, "Restaurant.language field missing"
    assert lang == "uz", f"Expected 'uz', got {lang!r}"


# ──────────────────────────────────────────
# TEST 2: GET public restaurant возвращает language
# ──────────────────────────────────────────
@pytest.mark.integration
def test_public_restaurant_response_includes_language(client, db, restaurant):
    """
    GET /api/restaurants/{slug} должен возвращать поле language.
    Фронтенд будет использовать его для загрузки нужного i18n JSON.
    """
    from models import Category

    cat = Category(restaurant_id=restaurant.id, name="Тест i18n", sort_order=99)
    db.add(cat)
    db.flush()

    resp = client.get(f"/api/restaurants/{restaurant.slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert "language" in data, "language field missing from public response"
    assert data["language"] == "uz"


# ──────────────────────────────────────────
# TEST 3: PATCH language="ru" работает
# ──────────────────────────────────────────
@pytest.mark.integration
def test_patch_language_ru(client, db, restaurant):
    """
    PATCH /api/restaurants/me/settings с language="ru" должен работать.
    """
    resp = client.patch("/api/restaurants/me/settings", json={"language": "ru"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "ru"

    # Проверяем что в БД тоже сохранилось
    db.refresh(restaurant)
    assert restaurant.language == "ru"


# ──────────────────────────────────────────
# TEST 4: PATCH language="en" работает
# ──────────────────────────────────────────
@pytest.mark.integration
def test_patch_language_en(client, db, restaurant):
    """
    PATCH /api/restaurants/me/settings с language="en" должен работать.
    """
    resp = client.patch("/api/restaurants/me/settings", json={"language": "en"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "en"

    db.refresh(restaurant)
    assert restaurant.language == "en"


# ──────────────────────────────────────────
# TEST 5: PATCH language="de" отклоняется
# ──────────────────────────────────────────
@pytest.mark.integration
def test_patch_language_de_rejected(client, restaurant):
    """
    PATCH с неподдерживаемым языком должен вернуть 400.
    """
    resp = client.patch("/api/restaurants/me/settings", json={"language": "de"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "de" in detail or "язык" in detail.lower() or "язык" in detail


# ──────────────────────────────────────────
# TEST 6: PATCH language="abc" отклоняется
# ──────────────────────────────────────────
@pytest.mark.integration
def test_patch_language_abc_rejected(client, restaurant):
    """
    PATCH с произвольной строкой должен вернуть 400.
    """
    resp = client.patch("/api/restaurants/me/settings", json={"language": "abc"})
    assert resp.status_code == 400


# ──────────────────────────────────────────
# TEST 7: PATCH language="fr" отклоняется
# ──────────────────────────────────────────
@pytest.mark.integration
def test_patch_language_fr_rejected(client, restaurant):
    """
    PATCH с французским языком (не в списке) должен вернуть 400.
    """
    resp = client.patch("/api/restaurants/me/settings", json={"language": "fr"})
    assert resp.status_code == 400


# ──────────────────────────────────────────
# TEST 8: GET settings возвращает language
# ──────────────────────────────────────────
@pytest.mark.integration
def test_get_settings_includes_language(client, restaurant):
    """
    GET /api/restaurants/me/settings должен возвращать поле language.
    """
    resp = client.get("/api/restaurants/me/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "language" in data, "language missing from settings response"
    assert data["language"] == "uz"


# ──────────────────────────────────────────
# TEST 9: PATCH language не трогает currency (Task 1A не сломан)
# ──────────────────────────────────────────
@pytest.mark.integration
def test_patch_language_does_not_affect_currency(client, db, restaurant):
    """
    Изменение language не должно влиять на currency.
    Проверяет совместимость с Task 1A.
    """
    # Убеждаемся что currency=UZS перед тестом
    assert restaurant.currency == "UZS"

    resp = client.patch("/api/restaurants/me/settings", json={"language": "ru"})
    assert resp.status_code == 200
    data = resp.json()

    # language обновился
    assert data["language"] == "ru"
    # currency не изменилась
    assert data["currency"] == "UZS"

    db.refresh(restaurant)
    assert restaurant.language == "ru"
    assert restaurant.currency == "UZS"


# ──────────────────────────────────────────
# TEST 10: language и currency можно изменить одновременно
# ──────────────────────────────────────────
@pytest.mark.integration
def test_patch_language_and_currency_together(client, db, restaurant):
    """
    PATCH может изменить language и currency в одном запросе.
    """
    resp = client.patch(
        "/api/restaurants/me/settings",
        json={"language": "ru", "currency": "RUB"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "ru"
    assert data["currency"] == "RUB"

    db.refresh(restaurant)
    assert restaurant.language == "ru"
    assert restaurant.currency == "RUB"


# ──────────────────────────────────────────
# TEST 11: PATCH uz — возврат к исходному языку
# ──────────────────────────────────────────
@pytest.mark.integration
def test_patch_language_back_to_uz(client, db, restaurant):
    """
    Можно вернуть язык обратно на "uz" после изменения.
    """
    # Сначала меняем на ru
    client.patch("/api/restaurants/me/settings", json={"language": "ru"})
    db.refresh(restaurant)
    assert restaurant.language == "ru"

    # Возвращаем на uz
    resp = client.patch("/api/restaurants/me/settings", json={"language": "uz"})
    assert resp.status_code == 200
    assert resp.json()["language"] == "uz"

    db.refresh(restaurant)
    assert restaurant.language == "uz"


# ──────────────────────────────────────────
# TEST 12: Public response возвращает обновлённый language
# ──────────────────────────────────────────
@pytest.mark.integration
def test_public_response_reflects_updated_language(client, db, restaurant):
    """
    После PATCH language, публичный GET отражает новое значение.
    """
    from models import Category

    # Меняем язык
    client.patch("/api/restaurants/me/settings", json={"language": "en"})
    db.refresh(restaurant)

    cat = Category(restaurant_id=restaurant.id, name="TestCat", sort_order=1)
    db.add(cat)
    db.flush()

    resp = client.get(f"/api/restaurants/{restaurant.slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "en"
