"""
tests/test_phase14_waiter_call.py — Phase 14
Tests: WaiterCall is_waiter_call_enabled (SEC-05) and notification.
Uses 'client' fixture (has Telegram auth, same as tg_client).
"""
import pytest
from unittest.mock import MagicMock, patch


class TestWaiterCallEnabledEnforcement:
    def test_disabled_location_returns_403(self, client, db, location, table):
        location.is_waiter_call_enabled = False
        db.commit()
        try:
            with patch("handlers.notify_waiter_call"):
                resp = client.post(
                    "/api/waiter-calls/",
                    json={"table_id": table.id},
                )
            assert resp.status_code == 403
        finally:
            location.is_waiter_call_enabled = True
            db.commit()

    def test_enabled_location_creates_call(self, client, db, location, table):
        location.is_waiter_call_enabled = True
        db.commit()
        with patch("handlers.notify_waiter_call"):
            resp = client.post(
                "/api/waiter-calls/",
                json={"table_id": table.id},
            )
        assert resp.status_code == 201


class TestWaiterCallNotification:
    def test_notify_called_after_create(self, client, db, location, table):
        location.is_waiter_call_enabled = True
        db.commit()
        with patch("handlers.notify_waiter_call") as mock_notify:
            resp = client.post(
                "/api/waiter-calls/",
                json={"table_id": table.id},
            )
            assert resp.status_code == 201
            mock_notify.assert_called_once()

    def test_notify_waiter_call_function(self):
        from handlers import notify_waiter_call
        call = MagicMock(id=42, table_id=5)
        table = MagicMock(table_number="7")
        location = MagicMock(
            id=1,
            telegram_dispatcher_id=123456,
            bot_token="fake:token",
        )
        with patch("handlers.get_location_bot") as mock_get_bot:
            mock_bot = MagicMock()
            mock_get_bot.return_value = mock_bot
            notify_waiter_call(call, table, location)
            mock_bot.send_message.assert_called_once()
            text = mock_bot.send_message.call_args[0][1]
            assert "7" in text
            assert "42" in text

    def test_notify_no_dispatcher_id_silent(self):
        from handlers import notify_waiter_call
        call = MagicMock(id=1, table_id=1)
        table = MagicMock(table_number="3")
        location = MagicMock(id=1, telegram_dispatcher_id=None)
        notify_waiter_call(call, table, location)  # must not raise

    def test_duplicate_call_blocked(self, client, db, location, table):
        location.is_waiter_call_enabled = True
        db.commit()
        with patch("handlers.notify_waiter_call"):
            r1 = client.post("/api/waiter-calls/", json={"table_id": table.id})
            assert r1.status_code == 201
            r2 = client.post("/api/waiter-calls/", json={"table_id": table.id})
            assert r2.status_code == 400
