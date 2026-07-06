"""
tests/test_multi_tenant.py — Multi-Tenant Isolation тесты

Это наиболее критичные тесты для SaaS.
Проверяют что Agency A не может видеть/изменять данные Agency B.

Покрывает:
  - Agency не видит рестораны другого агентства
  - Agency не может обновить ресторан другого агентства → 404
  - Agency не может удалить ресторан другого агентства → 404
  - Ресторан правильно привязан к agency_id из токена (не из body)
"""

import pytest


# ──────────────────────────────────────────
# TEST 28: Agency видит только свои рестораны
# ──────────────────────────────────────────
@pytest.mark.security
def test_agency_sees_only_own_restaurants(client, db, restaurant, restaurant2, agency_token):
    """
    Agency A (chinar) не должна видеть ресторан Palace (agency B).
    """
    resp = client.get(
        "/api/agency/restaurants",
        headers={"Authorization": f"Bearer {agency_token}"},
    )
    assert resp.status_code == 200
    slugs = [r["slug"] for r in resp.json()]
    assert "chinar" in slugs
    assert "palace" not in slugs  # изоляция


# ──────────────────────────────────────────
# TEST 29: Agency не может PATCH чужой ресторан
# ──────────────────────────────────────────
@pytest.mark.security
def test_agency_cannot_update_foreign_restaurant(client, db, restaurant2, agency_token):
    """
    Agency A не должна обновить ресторан Agency B.
    Ожидаем 404 — не 403 (не раскрываем существование ресурса).
    """
    resp = client.patch(
        f"/api/agency/restaurants/{restaurant2.id}",
        json={"name": "Hacked Name"},
        headers={"Authorization": f"Bearer {agency_token}"},
    )
    assert resp.status_code == 404


# ──────────────────────────────────────────
# TEST 30: Agency не может DELETE чужой ресторан
# ──────────────────────────────────────────
@pytest.mark.security
def test_agency_cannot_delete_foreign_restaurant(client, db, restaurant2, agency_token):
    resp = client.delete(
        f"/api/agency/restaurants/{restaurant2.id}",
        headers={"Authorization": f"Bearer {agency_token}"},
    )
    assert resp.status_code == 404


# ──────────────────────────────────────────
# TEST 31: Restaurant admin token scope
# ──────────────────────────────────────────
@pytest.mark.security
def test_restaurant_admin_token_contains_agency_id(restaurant, agency):
    """
    JWT ресторанного администратора содержит agency_id —
    для будущих проверок cross-agency доступа.
    """
    from auth import create_restaurant_token, decode_token

    token = create_restaurant_token(restaurant)
    payload = decode_token(token)

    assert payload["restaurant_id"] == restaurant.id
    assert payload["agency_id"] == agency.id
    assert payload["role"] == "restaurant_admin"


# ──────────────────────────────────────────
# TEST 32: Agency token wrong role → 403 on restaurant endpoints
# ──────────────────────────────────────────
@pytest.mark.security
def test_agency_token_rejected_on_restaurant_admin_endpoint(
    db, agency, restaurant, agency_token
):
    """
    Agency Owner токен не должен работать на эндпоинтах
    которые требуют restaurant_admin роль.
    """
    from api import app
    from auth import get_current_restaurant_admin, get_db
    from fastapi.testclient import TestClient

    # Убираем override get_current_restaurant_admin
    # чтобы использовалась реальная JWT-проверка
    overrides = {get_db: lambda: db}
    app.dependency_overrides = overrides

    with TestClient(app) as raw_client:
        resp = raw_client.get(
            f"/api/orders/restaurant/{restaurant.id}",
            headers={"Authorization": f"Bearer {agency_token}"},
        )
    # agency_owner роль не подходит для restaurant_admin эндпоинтов
    assert resp.status_code in (401, 403)

    app.dependency_overrides.clear()
