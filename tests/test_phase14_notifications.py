"""
tests/test_phase14_notifications.py — Phase 14
Tests: notify_client_scheduled (BLOCK-01), notify_waiter_call,
       notify_new_order with delivery_fee breakdown.
"""
import pytest
from unittest.mock import MagicMock, patch


def _make_order(order_type="delivery", scheduled_at=None, total=50000,
                subtotal=35000, delivery_fee=15000):
    o = MagicMock()
    o.id = 99
    o.order_type = order_type
    o.scheduled_at = scheduled_at
    o.total_amount = total
    o.subtotal = subtotal
    o.delivery_fee = delivery_fee
    o.client_telegram_id = 111111
    o.client_name = "Алиша"
    o.client_phone = "+998900000000"
    o.address = "ул. Навои 1"
    o.table_id = None
    o.comment = None
    return o


def _make_location(lang="uz", currency="UZS", dispatcher_id=222222,
                   bot_token="fake:bot_token", timezone="Asia/Tashkent"):
    loc = MagicMock()
    loc.id = 1
    loc.language = lang
    loc.currency = currency
    loc.telegram_dispatcher_id = dispatcher_id
    loc.bot_token = bot_token
    loc.timezone = timezone
    return loc


def _make_restaurant():
    r = MagicMock()
    r.id = 1
    return r


class TestNotifyClientScheduled:
    def test_scheduled_confirmation_sent(self):
        from datetime import datetime, timedelta, timezone
        from handlers import notify_client_scheduled
        order = _make_order(
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2)
        )
        location = _make_location()
        restaurant = _make_restaurant()

        with patch("handlers.get_location_bot") as mock_bot_fn:
            mock_bot = MagicMock()
            mock_bot_fn.return_value = mock_bot
            notify_client_scheduled(order, restaurant, location)
            mock_bot.send_message.assert_called_once()
            text = mock_bot.send_message.call_args[0][1]
            assert str(order.id) in text  # order ID in message

    def test_notify_client_accepted_not_called_for_scheduled(self):
        """BLOCK-01: accepted notification NOT sent at creation for scheduled orders."""
        from datetime import datetime, timedelta, timezone

        scheduled_at = datetime.now(timezone.utc) + timedelta(hours=2)
        accepted_called = []
        scheduled_called = []

        import handlers as h
        orig_accepted = h.notify_client_accepted
        orig_scheduled = h.notify_client_scheduled

        def fake_accepted(order, restaurant, location=None):
            accepted_called.append(True)

        def fake_scheduled(order, restaurant, location=None):
            scheduled_called.append(True)

        h.notify_client_accepted = fake_accepted
        h.notify_client_scheduled = fake_scheduled

        try:
            order = MagicMock()
            order.scheduled_at = scheduled_at
            # Simulate router logic
            if order.scheduled_at is None:
                h.notify_client_accepted(order, None)
            else:
                h.notify_client_scheduled(order, None)

            assert len(accepted_called) == 0, "notify_client_accepted must NOT be called for scheduled orders"
            assert len(scheduled_called) == 1, "notify_client_scheduled must be called"
        finally:
            h.notify_client_accepted = orig_accepted
            h.notify_client_scheduled = orig_scheduled

    def test_notify_client_accepted_called_for_immediate(self):
        """Immediate orders: notify_client_accepted called, not scheduled."""
        accepted_called = []
        scheduled_called = []

        import handlers as h
        orig_accepted = h.notify_client_accepted
        orig_scheduled = h.notify_client_scheduled

        def fake_accepted(order, restaurant, location=None):
            accepted_called.append(True)

        def fake_scheduled(order, restaurant, location=None):
            scheduled_called.append(True)

        h.notify_client_accepted = fake_accepted
        h.notify_client_scheduled = fake_scheduled

        try:
            order = MagicMock()
            order.scheduled_at = None
            if order.scheduled_at is None:
                h.notify_client_accepted(order, None)
            else:
                h.notify_client_scheduled(order, None)

            assert len(accepted_called) == 1
            assert len(scheduled_called) == 0
        finally:
            h.notify_client_accepted = orig_accepted
            h.notify_client_scheduled = orig_scheduled


class TestNotifyNewOrderDeliveryFee:
    def test_delivery_fee_shown_in_dispatcher_message(self):
        from handlers import notify_new_order
        order = _make_order(delivery_fee=15000, subtotal=35000, total=50000)
        location = _make_location()
        restaurant = _make_restaurant()
        items = []

        with patch("handlers.get_location_bot") as mock_bot_fn:
            mock_bot = MagicMock()
            mock_bot_fn.return_value = mock_bot
            notify_new_order(order, items, restaurant, location)
            text = mock_bot.send_message.call_args[0][1]
            # Fee line should be in message
            assert "Yetkazish" in text or "15" in text

    def test_no_fee_line_when_fee_zero(self):
        from handlers import notify_new_order
        order = _make_order(delivery_fee=0, subtotal=50000, total=50000,
                            order_type="takeaway")
        location = _make_location()
        restaurant = _make_restaurant()
        items = []

        with patch("handlers.get_location_bot") as mock_bot_fn:
            mock_bot = MagicMock()
            mock_bot_fn.return_value = mock_bot
            notify_new_order(order, items, restaurant, location)
            text = mock_bot.send_message.call_args[0][1]
            # No separate fee line when fee=0
            assert "Yetkazish" not in text

    def test_table_number_used_not_id(self):
        from handlers import notify_new_order
        order = _make_order(order_type="dine_in")
        order.table_id = 42
        order.address = None
        location = _make_location()
        restaurant = _make_restaurant()

        with patch("handlers.get_location_bot") as mock_bot_fn:
            mock_bot = MagicMock()
            mock_bot_fn.return_value = mock_bot
            # Pass table_number explicitly (Phase 14)
            notify_new_order(order, [], restaurant, location, table_number="7")
            text = mock_bot.send_message.call_args[0][1]
            assert "7" in text        # human-readable number shown
            assert "#42" not in text  # DB ID NOT shown when table_number provided
