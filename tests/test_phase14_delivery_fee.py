"""
tests/test_phase14_delivery_fee.py — Phase 14
Tests: delivery_fee included in Order.total_amount (AQ-01).
"""
import pytest
from fastapi import HTTPException
from models.delivery_zones import DeliveryZone
from models.tenant import Location


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
    try:
        db.delete(z)
        db.commit()
    except Exception:
        db.rollback()


class TestDeliveryFeeHelpers:
    def test_delivery_with_zone_fee(self, db, location, zone):
        from modules.cart.service import resolve_delivery_fee
        fee, zid = resolve_delivery_fee(db, location, "delivery", zone.id)
        assert fee == 15000
        assert zid == zone.id

    def test_delivery_without_zone_uses_location_fee(self, db, location):
        from modules.cart.service import resolve_delivery_fee
        location.delivery_fee = 10000
        db.commit()
        fee, zid = resolve_delivery_fee(db, location, "delivery", None)
        assert fee == 10000
        assert zid is None

    def test_takeaway_fee_is_zero(self, db, location):
        from modules.cart.service import resolve_delivery_fee
        fee, zid = resolve_delivery_fee(db, location, "takeaway", None)
        assert fee == 0
        assert zid is None

    def test_dine_in_fee_is_zero(self, db, location):
        from modules.cart.service import resolve_delivery_fee
        fee, zid = resolve_delivery_fee(db, location, "dine_in", None)
        assert fee == 0
        assert zid is None

    def test_nonexistent_zone_rejected(self, db, location):
        """zone_id that doesn't exist raises 404."""
        from modules.cart.service import resolve_delivery_fee
        with pytest.raises(HTTPException) as exc:
            resolve_delivery_fee(db, location, "delivery", 999999)
        assert exc.value.status_code == 404

    def test_inactive_zone_rejected(self, db, location, zone):
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

    def test_min_order_from_zone(self, db, location, zone):
        from modules.cart.service import resolve_effective_min_order
        zone.min_order = 50000
        db.commit()
        effective = resolve_effective_min_order(db, location, zone.id)
        assert effective == 50000

    def test_fee_snapshot_value(self, db, location, zone):
        """resolve_delivery_fee returns fee at call time."""
        from modules.cart.service import resolve_delivery_fee
        fee_at_checkout, _ = resolve_delivery_fee(db, location, "delivery", zone.id)
        assert fee_at_checkout == 15000


class TestScheduledAtValidation:
    def test_past_rejected(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import validate_scheduled_at
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(datetime.now(timezone.utc) - timedelta(hours=1))
        assert exc.value.status_code == 422

    def test_too_soon_rejected(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import validate_scheduled_at
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(datetime.now(timezone.utc) + timedelta(minutes=5))
        assert exc.value.status_code == 422

    def test_too_far_rejected(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import validate_scheduled_at
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(datetime.now(timezone.utc) + timedelta(days=10))
        assert exc.value.status_code == 422

    def test_valid_passes(self):
        from datetime import datetime, timedelta, timezone
        from modules.cart.service import validate_scheduled_at
        validate_scheduled_at(datetime.now(timezone.utc) + timedelta(hours=2))

    def test_none_passes(self):
        from modules.cart.service import validate_scheduled_at
        validate_scheduled_at(None)

    def test_naive_rejected(self):
        from datetime import datetime, timedelta
        from modules.cart.service import validate_scheduled_at
        with pytest.raises(HTTPException) as exc:
            validate_scheduled_at(datetime.now() + timedelta(hours=2))
        assert exc.value.status_code == 422
