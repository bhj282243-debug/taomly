"""
tests/test_phase14_security.py — Phase 14
Security tests.
"""
import pytest
from fastapi import HTTPException
from unittest.mock import patch


class TestClientCannotControlFee:
    def test_fee_sourced_from_db(self, db, location):
        from models.delivery_zones import DeliveryZone
        from modules.cart.service import resolve_delivery_fee
        zone = DeliveryZone(
            location_id=location.id, name="TestFee", fee=33000,
            is_active=True, sort_order=0, min_order=0,
        )
        db.add(zone)
        db.commit()
        try:
            fee, zid = resolve_delivery_fee(db, location, "delivery", zone.id)
            assert fee == 33000
            assert zid == zone.id
        finally:
            db.delete(zone)
            db.commit()

    def test_nonexistent_zone_rejected(self, db, location):
        from modules.cart.service import resolve_delivery_fee
        with pytest.raises(HTTPException) as exc:
            resolve_delivery_fee(db, location, "delivery", 999999)
        assert exc.value.status_code == 404


class TestScheduledAtBounds:
    def test_past_rejected(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import validate_scheduled_at
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(datetime.now(timezone.utc) - timedelta(minutes=5))
        assert exc.value.status_code == 422

    def test_beyond_horizon_rejected(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import (
            validate_scheduled_at, _SCHEDULED_MAX_HORIZON_DAYS,
        )
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(
                datetime.now(timezone.utc) + timedelta(days=_SCHEDULED_MAX_HORIZON_DAYS + 1)
            )
        assert exc.value.status_code == 422


class TestWaiterCallEnabledGate:
    def test_disabled_returns_403(self, client, db, location, table):
        location.is_waiter_call_enabled = False
        db.commit()
        try:
            with patch("handlers.notify_waiter_call"):
                resp = client.post(
                    "/api/waiter-calls/", json={"table_id": table.id}
                )
            assert resp.status_code == 403
        finally:
            location.is_waiter_call_enabled = True
            db.commit()


class TestInactiveZoneRejected:
    def test_inactive_zone_rejected(self, db, location):
        from models.delivery_zones import DeliveryZone
        from modules.cart.service import resolve_delivery_fee
        zone = DeliveryZone(
            location_id=location.id, name="Inactive", fee=5000,
            is_active=False, sort_order=0, min_order=0,
        )
        db.add(zone)
        db.commit()
        try:
            with pytest.raises(HTTPException) as exc:
                resolve_delivery_fee(db, location, "delivery", zone.id)
            assert exc.value.status_code == 404
        finally:
            db.delete(zone)
            db.commit()
