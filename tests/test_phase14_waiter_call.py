"""
tests/test_phase14_waiter_call.py — Phase 14
Tests: WaiterCall is_waiter_call_enabled enforcement (SEC-05)
       and Telegram notification (T-8).
"""
import pytest
from unittest.mock import MagicMock, patch, call as mock_call


class TestWaiterCallEnabledEnforcement:
    def test_disabled_location_returns_403(self, tg_client, db, location, table):
        """is_waiter_call_enabled=False → 403 Forbidden."""
        location.is_waiter_call_enabled = False
        db.commit()
        try:
            resp = tg_client.post(
                "/api/waiter-calls/",
                json={"table_id": table.id},
            )
            assert resp.status_code == 403
        finally:
            location.is_waiter_call_enabled = True
            db.commit()

    def test_enabled_location_creates_call(self, tg_client, db, location, table):
        """is_waiter_call_enabled=True → 201 Created."""
        location.is_waiter_call_enabled = True
        db.commit()
        with patch("handlers.notify_waiter_call"):
            resp = tg_client.post(
                "/api/waiter-calls/",
                json={"table_id": table.id},
            )
        assert resp.status_code == 201


class TestWaiterCallNotification:
    def test_notify_called_after_create(self, tg_client, db, location, table):
        """notify_waiter_call dispatched via BackgroundTasks after creation."""
        location.is_waiter_call_enabled = True
        db.commit()
        with patch("handlers.notify_waiter_call") as mock_notify:
            resp = tg_client.post(
                "/api/waiter-calls/",
                json={"table_id": table.id},
            )
            assert resp.status_code == 201
            # BackgroundTasks executes during TestClient call
            mock_notify.assert_called_once()

    def test_notify_waiter_call_function(self):
        """notify_waiter_call sends correct message format."""
        from handlers import notify_waiter_call

        call = MagicMock()
        call.id = 42
        call.table_id = 5

        table = MagicMock()
        table.table_number = "7"

        location = MagicMock()
        location.id = 1
        location.telegram_dispatcher_id = 123456
        location.bot_token = "fake:token"

        with patch("handlers.get_location_bot") as mock_get_bot:
            mock_bot = MagicMock()
            mock_get_bot.return_value = mock_bot
            notify_waiter_call(call, table, location)
            mock_bot.send_message.assert_called_once()
            text = mock_bot.send_message.call_args[0][1]
            assert "7" in text        # table_number in text
            assert "42" in text       # call.id in text

    def test_notify_no_dispatcher_id_silent(self):
        """Missing dispatcher_id → warning only, no exception."""
        from handlers import notify_waiter_call
        call = MagicMock(id=1, table_id=1)
        table = MagicMock(table_number="3")
        location = MagicMock(id=1, telegram_dispatcher_id=None)
        # Should not raise
        notify_waiter_call(call, table, location)

    def test_duplicate_call_still_blocked(self, tg_client, db, location, table):
        """Existing active call blocks second call (regression)."""
        location.is_waiter_call_enabled = True
        db.commit()
        with patch("handlers.notify_waiter_call"):
            r1 = tg_client.post("/api/waiter-calls/", json={"table_id": table.id})
            assert r1.status_code == 201
            r2 = tg_client.post("/api/waiter-calls/", json={"table_id": table.id})
            assert r2.status_code == 400
