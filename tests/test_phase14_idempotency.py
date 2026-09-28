"""
tests/test_phase14_idempotency.py — Phase 14
Tests: Cart→Order direct FK (SEC-03 fix).
"""
import pytest
from typing import Generator
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

SESSION_IDEM = "idem1234-idem-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def checkout_client_idem(db, restaurant, location, tg_user) -> Generator:
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
        "X-Cart-Session": SESSION_IDEM,
    }
    with TestClient(app, raise_server_exceptions=True, headers=headers) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def seeded_cart_idem(checkout_client_idem, product):
    resp = checkout_client_idem.post(
        "/api/cart/items",
        json={"product_id": product.id, "quantity": 1},
    )
    assert resp.status_code in (200, 201)
    return resp.json()


class TestCartOrderLink:
    def test_cart_order_id_set_after_checkout(
        self, checkout_client_idem, product, db, seeded_cart_idem
    ):
        from modules.cart.models import Cart
        resp = checkout_client_idem.post("/api/cart/checkout", json={
            "order_type": "takeaway",
            "client_name": "Test",
            "client_phone": "+998900000001",
            "idempotency_key": "p14-idem-cart-link-001",
        })
        assert resp.status_code == 201
        order_id = resp.json()["id"]

        cart = db.query(Cart).filter(
            Cart.status == "checked_out",
            Cart.order_id == order_id,
        ).first()
        assert cart is not None
        assert cart.order_id == order_id


class TestDirectFKLookup:
    def test_find_order_uses_direct_fk(self, db):
        from modules.cart.service import _find_order_for_checked_out_cart

        cart = MagicMock()
        cart.order_id = 12345
        cart.currency = "UZS"

        order_mock = MagicMock()
        db_mock = MagicMock()
        db_mock.query.return_value.filter.return_value.first.return_value = order_mock

        result = _find_order_for_checked_out_cart(db_mock, cart, 1)
        assert result is order_mock

    def test_legacy_fallback_when_no_order_id(self, db):
        from modules.cart.service import _find_order_for_checked_out_cart

        cart = MagicMock()
        cart.order_id = None
        cart.currency = "UZS"

        db_mock = MagicMock()
        (db_mock.query.return_value.filter.return_value
         .order_by.return_value.limit.return_value.first.return_value) = None

        result = _find_order_for_checked_out_cart(db_mock, cart, 1)
        assert result is None
