"""
tests/test_phase14_web_dine_in.py — Phase 14
Tests: Public Web DINE_IN — table resolve endpoint.
"""
import pytest
from typing import Generator
from fastapi.testclient import TestClient

SESSION_P14 = "p14p14p14-dine-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def checkout_client_p14(db, restaurant, location, tg_user) -> Generator:
    """Checkout client для Phase 14 DINE_IN тестов."""
    from api import app
    from auth import get_telegram_user
    from database import get_db

    def override_db():
        yield db

    def override_tg():
        return tg_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_telegram_user] = override_tg

    headers = {
        "X-Restaurant-Id": str(restaurant.id),
        "X-Location-Id": str(location.id),
        "X-Cart-Session": SESSION_P14,
    }
    with TestClient(app, raise_server_exceptions=True, headers=headers) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def seeded_cart_p14(checkout_client_p14, product):
    """Кладём товар в корзину для Phase 14 тестов."""
    resp = checkout_client_p14.post(
        "/api/cart/items",
        json={"product_id": product.id, "quantity": 1},
    )
    assert resp.status_code in (200, 201)
    return resp.json()


class TestTableResolveEndpoint:
    def test_valid_table_returns_table_id(self, client, location, table):
        resp = client.get(
            f"/api/restaurants/{location.slug}/table/{table.table_number}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["table_id"] == table.id
        assert data["table_number"] == table.table_number
        assert data["location_id"] == location.id

    def test_wrong_slug_returns_404(self, client, table):
        resp = client.get(
            f"/api/restaurants/nonexistent-p14-xyz/table/{table.table_number}"
        )
        assert resp.status_code == 404

    def test_wrong_table_number_returns_404(self, client, location):
        resp = client.get(
            f"/api/restaurants/{location.slug}/table/99999"
        )
        assert resp.status_code == 404

    def test_response_contains_required_fields(self, client, location, table):
        resp = client.get(
            f"/api/restaurants/{location.slug}/table/{table.table_number}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "table_number" in data
        assert "table_id" in data
        assert "location_id" in data


class TestWebCheckoutDineIn:
    def test_dine_in_checkout_with_table_id(
        self, checkout_client_p14, product, db, seeded_cart_p14, table
    ):
        resp = checkout_client_p14.post("/api/cart/checkout", json={
            "order_type": "dine_in",
            "table_id": table.id,
            "idempotency_key": "p14-dine-in-web-001",
        })
        assert resp.status_code == 201
        assert resp.json()["order_type"] == "dine_in"

    def test_invalid_table_id_rejected(
        self, checkout_client_p14, product, db, seeded_cart_p14
    ):
        resp = checkout_client_p14.post("/api/cart/checkout", json={
            "order_type": "dine_in",
            "table_id": 999999,
            "idempotency_key": "p14-dine-in-bad-001",
        })
        assert resp.status_code in (400, 404)
