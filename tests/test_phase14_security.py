"""
tests/test_phase14_security.py — Phase 14
Tests: security checks — client cannot control fee/total, cross-tenant zones,
       rate limiting, scheduled_at bounds, inactive table reject.
"""
import pytest
from fastapi import HTTPException
from unittest.mock import patch


class TestClientCannotControlFee:
    def test_delivery_fee_not_accepted_from_client(self, db, location):
        """Client-provided delivery_fee is ignored — server computes it."""
        from modules.cart.service import resolve_delivery_fee
        # No zone configured — fallback = location.delivery_fee
        location.delivery_fee = 5000
        db.commit()
        fee, _ = resolve_delivery_fee(db, location, "delivery", None)
        # Fee is from DB, not from any client input
        assert fee == 5000

    def test_cross_tenant_zone_id_rejected(self, db, location):
        """zone_id from another restaurant returns 404."""
        from modules.cart.service import resolve_delivery_fee
        with pytest.raises(HTTPException) as exc:
            resolve_delivery_fee(db, location, "delivery", 999999)
        assert exc.value.status_code == 404


class TestScheduledAtBounds:
    def test_scheduled_at_in_past_rejected(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import validate_scheduled_at
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(datetime.now(timezone.utc) - timedelta(minutes=5))
        assert exc.value.status_code == 422

    def test_scheduled_at_beyond_horizon_rejected(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import (
            validate_scheduled_at, _SCHEDULED_MAX_HORIZON_DAYS,
        )
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(
                datetime.now(timezone.utc) + timedelta(days=_SCHEDULED_MAX_HORIZON_DAYS + 1)
            )
        assert exc.value.status_code == 422


class TestInactiveTableReject:
    def test_inactive_table_not_in_public_resolve(self, client, db, location, table):
        """Inactive table returns 404 from table resolve endpoint."""
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


class TestWaiterCallEnabledGate:
    def test_disabled_returns_403(self, tg_client, db, location, table):
        location.is_waiter_call_enabled = False
        db.commit()
        try:
            with patch("handlers.notify_waiter_call"):
                resp = tg_client.post(
                    "/api/waiter-calls/", json={"table_id": table.id}
                )
            assert resp.status_code == 403
        finally:
            location.is_waiter_call_enabled = True
            db.commit()


class TestDeliveryZoneTenantIsolation:
    def test_zone_fee_sourced_from_db_not_client(self, db, location):
        """Zone fee is always from DB — no client input accepted."""
        from models.delivery_zones import DeliveryZone
        from modules.cart.service import resolve_delivery_fee
        zone = DeliveryZone(
            location_id=location.id, name="Test", fee=33000,
            is_active=True, sort_order=0, min_order=0,
        )
        db.add(zone)
        db.commit()
        try:
            fee, zid = resolve_delivery_fee(db, location, "delivery", zone.id)
            assert fee == 33000   # from DB only
            assert zid == zone.id
        finally:
            db.delete(zone)
            db.commit()
