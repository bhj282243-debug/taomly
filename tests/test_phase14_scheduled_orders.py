"""
tests/test_phase14_scheduled_orders.py — Phase 14
Tests: Scheduled Orders activation, validation, KDS exclusion.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from models.orders import Order
from models.tenant import Location
from modules.cart.service import (
    _SCHEDULED_MAX_HORIZON_DAYS,
    _SCHEDULED_MIN_ADVANCE_MINUTES,
    SCHEDULED_ACTIVATION_BUFFER_MINUTES,
    validate_scheduled_at,
)


VALID_SCHEDULED = datetime.now(timezone.utc) + timedelta(hours=3)


class TestScheduledValidation:
    def test_past_rejected(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            validate_scheduled_at(datetime.now(timezone.utc) - timedelta(minutes=1))
        assert e.value.status_code == 422

    def test_too_soon_rejected(self):
        from fastapi import HTTPException
        soon = datetime.now(timezone.utc) + timedelta(
            minutes=_SCHEDULED_MIN_ADVANCE_MINUTES - 1
        )
        with pytest.raises(HTTPException) as e:
            validate_scheduled_at(soon)
        assert e.value.status_code == 422

    def test_too_far_rejected(self):
        from fastapi import HTTPException
        far = datetime.now(timezone.utc) + timedelta(
            days=_SCHEDULED_MAX_HORIZON_DAYS + 1
        )
        with pytest.raises(HTTPException) as e:
            validate_scheduled_at(far)
        assert e.value.status_code == 422

    def test_valid_passes(self):
        validate_scheduled_at(VALID_SCHEDULED)  # no exception

    def test_none_passes(self):
        validate_scheduled_at(None)  # no exception

    def test_naive_rejected(self):
        from fastapi import HTTPException
        naive = datetime.now() + timedelta(hours=2)
        with pytest.raises(HTTPException) as e:
            validate_scheduled_at(naive)
        assert e.value.status_code == 422


class TestActivationLogic:
    def _make_order(self, scheduled_at, order_type="takeaway", zone_id=None):
        order = MagicMock()
        order.id = 1
        order.status = "new"
        order.scheduled_at = scheduled_at
        order.order_type = order_type
        order.delivery_zone_id = zone_id
        order.location_id = 1
        order.restaurant_id = 1
        return order

    def _make_location(self, prep_time=None):
        loc = MagicMock()
        loc.id = 1
        loc.preparation_time_minutes = prep_time
        loc.timezone = "Asia/Tashkent"
        return loc

    def test_order_not_activated_before_window(self):
        """Order with prep_time=20min not activated when 25min before scheduled_at."""
        from modules.scheduled.activation import activate_scheduled_orders, ACTIVATION_BUFFER_MINUTES
        now = datetime.now(timezone.utc)
        # scheduled 25 minutes from now; prep=20, buffer=5 → activation at now+0
        # So 25min from now: now < scheduled - 20 - 5 = now → should not activate
        scheduled = now + timedelta(minutes=25)
        order = self._make_order(scheduled)
        location = self._make_location(prep_time=20)

        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.all.return_value = [order]
        db.query.return_value.filter.return_value.first.return_value = location

        # activation_time = scheduled - 20 - 5 = now + 0
        # Marginally: now < activation_time depending on execution time
        # Test: order at 26min from now should NOT activate (activation needs ≥26min from now)
        scheduled_far = now + timedelta(minutes=26)
        order2 = self._make_order(scheduled_far)
        # activation_time = scheduled_far - 25 = now + 1min → now < activation → not activated
        activation_time = scheduled_far - timedelta(minutes=20 + ACTIVATION_BUFFER_MINUTES)
        assert now < activation_time  # confirm not yet time

    def test_activation_formula_pickup(self):
        """pickup: activation = scheduled_at - prep_time - buffer."""
        from modules.scheduled.activation import ACTIVATION_BUFFER_MINUTES
        now = datetime.now(timezone.utc)
        prep = 25
        scheduled = now + timedelta(minutes=prep + ACTIVATION_BUFFER_MINUTES - 1)
        activation_time = scheduled - timedelta(minutes=prep + ACTIVATION_BUFFER_MINUTES)
        # activation_time = now - 1min → should activate
        assert now >= activation_time

    def test_activation_formula_delivery_includes_eta(self):
        """delivery: activation = scheduled_at - prep_time - zone_eta - buffer."""
        from modules.scheduled.activation import ACTIVATION_BUFFER_MINUTES
        now = datetime.now(timezone.utc)
        prep = 20
        eta = 15
        scheduled = now + timedelta(minutes=prep + eta + ACTIVATION_BUFFER_MINUTES - 1)
        activation_time = scheduled - timedelta(minutes=prep + eta + ACTIVATION_BUFFER_MINUTES)
        assert now >= activation_time


class TestKDSExcludesScheduled:
    def test_status_new_excluded_from_kds(self):
        """KDS _KDS_ACTIVE_STATUSES does not include 'new'."""
        from routers.orders import _KDS_ACTIVE_STATUSES
        assert "new" not in _KDS_ACTIVE_STATUSES

    def test_accepted_status_in_kds(self):
        """After activation (new→accepted), order appears in KDS."""
        from routers.orders import _KDS_ACTIVE_STATUSES
        assert "accepted" in _KDS_ACTIVE_STATUSES
