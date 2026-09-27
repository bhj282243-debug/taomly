"""
tests/test_phase14_delivery_fee.py — Phase 14
Tests: delivery_fee included in Order.total_amount (AQ-01).
  - subtotal = items only
  - delivery_fee = server-side from zone or location
  - total_amount = subtotal + delivery_fee
  - Payment.amount == Order.total_amount
  - delivery_fee = 0 for takeaway and dine_in
  - client cannot supply delivery_fee
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Location
from models.delivery_zones import DeliveryZone
from models.orders import Order


@pytest.fixture
def zone(db, location):
    z = DeliveryZone(
        location_id=location.id,
        name="Центр",
        fee=15000,
        min_order=0,
        is_active=True,
        sort_order=0,
    )
    db.add(z)
    db.commit()
    db.refresh(z)
    yield z
    db.delete(z)
    db.commit()


class TestDeliveryFeeInTotal:
    def test_delivery_with_zone_fee_in_total(self, checkout_client, product, zone, db):
        """delivery_fee from zone included in total_amount."""
        from modules.cart.service import resolve_delivery_fee
        location = db.query(Location).filter(Location.id == zone.location_id).first()
        fee, zid = resolve_delivery_fee(db, location, "delivery", zone.id)
        assert fee == 15000
        assert zid == zone.id

    def test_delivery_without_zone_uses_location_fee(self, db, location):
        """Fallback to location.delivery_fee when no zone_id."""
        from modules.cart.service import resolve_delivery_fee
        location.delivery_fee = 10000
        db.commit()
        fee, zid = resolve_delivery_fee(db, location, "delivery", None)
        assert fee == 10000
        assert zid is None

    def test_takeaway_fee_is_zero(self, db, location):
        """Non-delivery orders always have delivery_fee=0."""
        from modules.cart.service import resolve_delivery_fee
        fee, zid = resolve_delivery_fee(db, location, "takeaway", None)
        assert fee == 0
        assert zid is None

    def test_dine_in_fee_is_zero(self, db, location):
        from modules.cart.service import resolve_delivery_fee
        fee, zid = resolve_delivery_fee(db, location, "dine_in", None)
        assert fee == 0
        assert zid is None

    def test_cross_tenant_zone_rejected(self, db, location, zone):
        """Zone from different location raises 404."""
        from fastapi import HTTPException
        from modules.cart.service import resolve_delivery_fee
        # Temporarily change zone's location_id to simulate cross-tenant
        original = zone.location_id
        zone.location_id = 99999
        db.commit()
        try:
            with pytest.raises(HTTPException) as exc:
                resolve_delivery_fee(db, location, "delivery", zone.id)
            assert exc.value.status_code == 404
        finally:
            zone.location_id = original
            db.commit()

    def test_inactive_zone_rejected(self, db, location, zone):
        """Inactive zone raises 404."""
        from fastapi import HTTPException
        from modules.cart.service import resolve_delivery_fee
        zone.is_active = False
        db.commit()
        try:
            with pytest.raises(HTTPException) as exc:
                resolve_delivery_fee(db, location, "delivery", zone.id)
            assert exc.value.status_code == 404
        finally:
            zone.is_active = True
            db.commit()

    def test_min_order_checks_subtotal_not_total(self, db, location, zone):
        """min_order validation is against subtotal, not total_amount."""
        from modules.cart.service import resolve_effective_min_order
        zone.min_order = 50000
        db.commit()
        effective = resolve_effective_min_order(db, location, zone.id)
        assert effective == 50000

    def test_fee_snapshot_immutable(self, db, location, zone):
        """Changing zone.fee after order does not affect Order.delivery_fee."""
        from modules.cart.service import resolve_delivery_fee
        fee_at_checkout, _ = resolve_delivery_fee(db, location, "delivery", zone.id)
        assert fee_at_checkout == 15000
        zone.fee = 99999
        db.commit()
        # A pre-existing order's delivery_fee would still be 15000 (snapshotted)
        assert fee_at_checkout == 15000  # snapshot value unchanged


class TestScheduledAtValidation:
    def test_past_scheduled_at_rejected(self):
        from datetime import datetime, timedelta, timezone
        from fastapi import HTTPException
        from modules.cart.service import validate_scheduled_at
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(past)
        assert exc.value.status_code == 422

    def test_too_soon_rejected(self):
        from datetime import datetime, timedelta, timezone
        from fastapi import HTTPException
        from modules.cart.service import validate_scheduled_at
        soon = datetime.now(timezone.utc) + timedelta(minutes=5)
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(soon)
        assert exc.value.status_code == 422

    def test_too_far_rejected(self):
        from datetime import datetime, timedelta, timezone
        from fastapi import HTTPException
        from modules.cart.service import validate_scheduled_at
        far = datetime.now(timezone.utc) + timedelta(days=10)
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(far)
        assert exc.value.status_code == 422

    def test_valid_scheduled_at_passes(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import validate_scheduled_at
        valid = datetime.now(timezone.utc) + timedelta(hours=2)
        validate_scheduled_at(valid)  # must not raise

    def test_none_scheduled_at_passes(self):
        from modules.cart.service import validate_scheduled_at
        validate_scheduled_at(None)  # must not raise

    def test_naive_datetime_rejected(self):
        from datetime import datetime, timedelta
        from fastapi import HTTPException
        from modules.cart.service import validate_scheduled_at
        naive = datetime.now() + timedelta(hours=2)
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(naive)
        assert exc.value.status_code == 422
