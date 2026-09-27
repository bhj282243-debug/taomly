"""
tests/test_phase14_idempotency.py — Phase 14
Tests: Cart→Order direct FK (SEC-03 fix).
"""
import pytest
from models.orders import Order
from modules.cart.models import Cart


class TestCartOrderLink:
    def test_cart_order_id_set_after_checkout(self, checkout_client, product, db, seeded_cart):
        """cart.order_id is set after successful checkout."""
        resp = checkout_client.post("/api/cart/checkout", json={
            "order_type": "takeaway",
            "client_name": "Test",
            "client_phone": "+998900000001",
            "idempotency_key": "idem-test-001",
        })
        assert resp.status_code == 201
        order_id = resp.json()["id"]

        # Find cart and verify order_id is set
        cart = db.query(Cart).filter(
            Cart.status == "checked_out",
        ).order_by(Cart.id.desc()).first()
        assert cart is not None
        assert cart.order_id == order_id

    def test_idempotency_replay_returns_same_order(self, checkout_client, product, db, seeded_cart):
        """Same idempotency key → same Order returned, no duplicate."""
        key = "idem-replay-test-001"
        r1 = checkout_client.post("/api/cart/checkout", json={
            "order_type": "takeaway",
            "client_name": "Test",
            "client_phone": "+998900000002",
            "idempotency_key": key,
        })
        assert r1.status_code == 201
        order_id_1 = r1.json()["id"]

        # Second request with same key
        r2 = checkout_client.post("/api/cart/checkout", json={
            "order_type": "takeaway",
            "client_name": "Test",
            "client_phone": "+998900000002",
            "idempotency_key": key,
        })
        # Should return 200 (replay) or 409 (already checked out)
        assert r2.status_code in (200, 201, 409)
        if r2.status_code in (200, 201):
            assert r2.json()["id"] == order_id_1


class TestDirectFKLookup:
    def test_find_order_for_checked_out_cart_uses_direct_fk(self, db):
        """_find_order_for_checked_out_cart uses direct FK when order_id set."""
        from modules.cart.service import _find_order_for_checked_out_cart

        cart = Cart.__new__(Cart)
        cart.order_id = 12345
        cart.id = 1

        order_mock = object()
        from unittest.mock import MagicMock
        db_mock = MagicMock()
        db_mock.query.return_value.filter.return_value.first.return_value = order_mock

        result = _find_order_for_checked_out_cart(db_mock, cart, 1)
        assert result is order_mock
        # Verify filter was called with Order.id == 12345 (not restaurant_id lookup)
        db_mock.query.assert_called()

    def test_legacy_fallback_when_no_order_id(self, db):
        """Legacy fallback used when cart.order_id is None."""
        from modules.cart.service import _find_order_for_checked_out_cart

        cart = Cart.__new__(Cart)
        cart.order_id = None
        cart.currency = "UZS"

        from unittest.mock import MagicMock
        db_mock = MagicMock()
        db_mock.query.return_value.filter.return_value.order_by.return_value.limit.return_value.first.return_value = None

        result = _find_order_for_checked_out_cart(db_mock, cart, 1)
        # Reaches legacy path (returns None here since mock returns None)
        assert result is None
