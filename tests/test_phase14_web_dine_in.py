"""
tests/test_phase14_web_dine_in.py — Phase 14
Tests: Public Web DINE_IN — table resolve endpoint and is_active filter.
"""
import pytest


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
        resp = client.get(f"/api/restaurants/nonexistent-xyz/table/{table.table_number}")
        assert resp.status_code == 404

    def test_wrong_table_number_returns_404(self, client, location):
        resp = client.get(f"/api/restaurants/{location.slug}/table/9999")
        assert resp.status_code == 404

    def test_inactive_table_returns_404(self, client, db, location, table):
        """Phase 14: inactive table not resolvable via endpoint."""
        table.is_active = False
        db.commit()
        try:
            resp = client.get(
                f"/api/restaurants/{location.slug}/table/{table.table_number}"
            )
            assert resp.status_code == 404
        finally:
            table.is_active = True
            db.commit()

    def test_table_from_other_location_returns_404(self, client, db, restaurant, location, table):
        """Table number exists but under different location — wrong slug."""
        resp = client.get(
            f"/api/restaurants/nonexistent-location-slug/table/{table.table_number}"
        )
        assert resp.status_code == 404

    def test_response_contains_table_number(self, client, location, table):
        """Response uses table_number (human-readable), not only DB id."""
        resp = client.get(
            f"/api/restaurants/{location.slug}/table/{table.table_number}"
        )
        assert resp.status_code == 200
        assert "table_number" in resp.json()
        assert "table_id" in resp.json()


class TestWebCheckoutDineIn:
    def test_dine_in_checkout_with_table_id(self, checkout_client, product, db, seeded_cart, table):
        """Web checkout with dine_in + table_id → 201."""
        resp = checkout_client.post("/api/cart/checkout", json={
            "order_type": "dine_in",
            "table_id": table.id,
            "idempotency_key": "web-dine-in-001",
        })
        assert resp.status_code == 201
        assert resp.json()["order_type"] == "dine_in"

    def test_cross_location_table_rejected(self, checkout_client, product, db, seeded_cart):
        """table_id from different location → 400/404."""
        resp = checkout_client.post("/api/cart/checkout", json={
            "order_type": "dine_in",
            "table_id": 999999,
            "idempotency_key": "web-dine-in-cross-001",
        })
        assert resp.status_code in (400, 404)
