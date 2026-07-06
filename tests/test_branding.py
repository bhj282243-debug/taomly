"""
tests/test_branding.py — White Label Branding & Dynamic Manifest

Покрывает:
  - GET /manifest/{slug}.json возвращает данные ресторана
  - manifest содержит правильные цвета ресторана
  - manifest содержит правильное имя ресторана
  - manifest содержит правильный start_url со slug
  - fallback manifest для несуществующего slug
  - fallback manifest валидный JSON с корректной структурой
  - короткое имя не превышает 12 символов
  - ресторан без logo_url не ломает manifest
  - multi-tenant: два ресторана получают разные манифесты
"""

import pytest


# ──────────────────────────────────────────
# TEST B1: manifest возвращает имя ресторана
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_returns_restaurant_name(client, restaurant):
    resp = client.get(f"/manifest/{restaurant.slug}.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == restaurant.name


# ──────────────────────────────────────────
# TEST B2: manifest содержит primary_color как theme_color
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_theme_color_matches_primary(client, restaurant):
    resp = client.get(f"/manifest/{restaurant.slug}.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["theme_color"] == restaurant.primary_color


# ──────────────────────────────────────────
# TEST B3: manifest содержит secondary_color как background_color
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_background_color_matches_secondary(client, restaurant):
    resp = client.get(f"/manifest/{restaurant.slug}.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["background_color"] == restaurant.secondary_color


# ──────────────────────────────────────────
# TEST B4: start_url содержит slug ресторана
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_start_url_contains_slug(client, restaurant):
    resp = client.get(f"/manifest/{restaurant.slug}.json")
    assert resp.status_code == 200
    data = resp.json()
    assert restaurant.slug in data["start_url"]


# ──────────────────────────────────────────
# TEST B5: short_name не превышает 12 символов
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_short_name_max_12_chars(client, restaurant):
    resp = client.get(f"/manifest/{restaurant.slug}.json")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["short_name"]) <= 12


# ──────────────────────────────────────────
# TEST B6: fallback для несуществующего slug — 200, не 404
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_fallback_for_unknown_slug(client):
    resp = client.get("/manifest/nonexistent-restaurant.json")
    assert resp.status_code == 200
    data = resp.json()
    # Fallback manifest должен иметь корректную структуру
    assert "name" in data
    assert "icons" in data
    assert "start_url" in data


# ──────────────────────────────────────────
# TEST B7: fallback manifest имеет корректные иконки
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_fallback_has_valid_icons(client):
    resp = client.get("/manifest/nonexistent.json")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["icons"], list)
    assert len(data["icons"]) >= 2
    for icon in data["icons"]:
        assert "src" in icon
        assert "sizes" in icon
        assert "type" in icon


# ──────────────────────────────────────────
# TEST B8: manifest реального ресторана имеет иконки
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_real_restaurant_has_icons(client, restaurant):
    resp = client.get(f"/manifest/{restaurant.slug}.json")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["icons"], list)
    assert len(data["icons"]) >= 2


# ──────────────────────────────────────────
# TEST B9: multi-tenant — два ресторана получают разные манифесты
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_multitenant_isolation(client, restaurant, restaurant2):
    resp1 = client.get(f"/manifest/{restaurant.slug}.json")
    resp2 = client.get(f"/manifest/{restaurant2.slug}.json")

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    data1 = resp1.json()
    data2 = resp2.json()

    # Разные рестораны — разные манифесты
    assert data1["name"] != data2["name"]
    assert data1["theme_color"] != data2["theme_color"]
    assert data1["start_url"] != data2["start_url"]


# ──────────────────────────────────────────
# TEST B10: content-type корректный для PWA manifest
# ──────────────────────────────────────────
@pytest.mark.integration
def test_manifest_content_type(client, restaurant):
    resp = client.get(f"/manifest/{restaurant.slug}.json")
    assert resp.status_code == 200
    assert "application/manifest+json" in resp.headers.get("content-type", "")
