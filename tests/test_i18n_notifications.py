"""
tests/test_i18n_notifications.py — Task 1B.4

Тесты параметризации Telegram customer notifications через i18n.py.

Запускаются без PostgreSQL и без реального Telegram API.
Все notify_client_* функции тестируются через mock.
"""

import pytest
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_i18n_cache():
    """Изолируем i18n кеш между тестами."""
    from i18n import clear_cache
    clear_cache()
    yield
    clear_cache()


def _make_restaurant(language="uz", currency="UZS"):
    """Фабрика mock-ресторана с нужными полями."""
    r = MagicMock()
    r.language = language
    r.currency = currency
    r.name = "Test Restaurant"
    r.id = 1
    r.bot_token = None  # заглушим бот
    return r


def _make_order(order_id=42, order_type="delivery", total=50000, client_telegram_id=123456):
    """Фабрика mock-заказа."""
    o = MagicMock()
    o.id = order_id
    o.order_type = order_type
    o.total_amount = total
    o.client_telegram_id = client_telegram_id
    return o


# ──────────────────────────────────────────
# ХЕЛПЕР: вызвать notify_* и поймать текст
# ──────────────────────────────────────────

def _call_notify(func_name, *args, **kwargs):
    """
    Вызывает handlers.<func_name>(*args) и возвращает текст,
    переданный в _notify_client, без реального Telegram API.
    """
    captured = {}

    def fake_notify_client(order, restaurant, text, event_name):
        captured["text"] = text
        captured["event"] = event_name

    with patch("handlers._notify_client", side_effect=fake_notify_client):
        import handlers
        getattr(handlers, func_name)(*args, **kwargs)

    return captured.get("text", "")


# ══════════════════════════════════════════
# ГРУППА 1: notify_client_accepted
# ══════════════════════════════════════════

class TestNotifyClientAccepted:

    def test_uz_language(self):
        """UZ: содержит UZ текст."""
        r = _make_restaurant(language="uz")
        o = _make_order(order_type="delivery")
        text = _call_notify("notify_client_accepted", o, r)
        assert "qabul qilindi" in text
        assert "yetkazib beriladi" in text
        assert str(o.id) in text

    def test_ru_language(self):
        """RU: содержит RU текст."""
        r = _make_restaurant(language="ru")
        o = _make_order(order_type="delivery")
        text = _call_notify("notify_client_accepted", o, r)
        assert "принят" in text
        assert "доставлен" in text or "будет доставлен" in text
        assert str(o.id) in text

    def test_en_language(self):
        """EN: содержит EN текст."""
        r = _make_restaurant(language="en")
        o = _make_order(order_type="delivery")
        text = _call_notify("notify_client_accepted", o, r)
        assert "accepted" in text
        assert str(o.id) in text

    def test_unknown_language_fallback_uz(self):
        """Неизвестный язык → fallback uz."""
        r = _make_restaurant(language="de")
        o = _make_order(order_type="delivery")
        text = _call_notify("notify_client_accepted", o, r)
        # UZ fallback — текст должен быть непустым и содержать id
        assert text
        assert str(o.id) in text

    def test_order_type_takeaway_uz(self):
        """UZ takeaway: содержит правильный action."""
        r = _make_restaurant(language="uz")
        o = _make_order(order_type="takeaway")
        text = _call_notify("notify_client_accepted", o, r)
        assert "olib ketishingiz mumkin" in text

    def test_order_type_dine_in_uz(self):
        """UZ dine_in: содержит action."""
        r = _make_restaurant(language="uz")
        o = _make_order(order_type="dine_in")
        text = _call_notify("notify_client_accepted", o, r)
        assert "tayyorlanmoqda" in text

    def test_unknown_order_type_fallback(self):
        """Неизвестный order_type → default action, не крашится."""
        r = _make_restaurant(language="uz")
        o = _make_order(order_type="unknown_future_type")
        text = _call_notify("notify_client_accepted", o, r)
        assert text  # не пустой

    def test_none_language_fallback(self):
        """language=None → uz fallback."""
        r = _make_restaurant(language=None)
        o = _make_order()
        text = _call_notify("notify_client_accepted", o, r)
        assert text
        assert str(o.id) in text


# ══════════════════════════════════════════
# ГРУППА 2: notify_client_preparing
# ══════════════════════════════════════════

class TestNotifyClientPreparing:

    def test_uz_language(self):
        r = _make_restaurant(language="uz")
        o = _make_order()
        text = _call_notify("notify_client_preparing", o, r)
        assert "tayyorlanmoqda" in text
        assert str(o.id) in text

    def test_ru_language(self):
        r = _make_restaurant(language="ru")
        o = _make_order()
        text = _call_notify("notify_client_preparing", o, r)
        assert "готовится" in text
        assert str(o.id) in text

    def test_en_language(self):
        r = _make_restaurant(language="en")
        o = _make_order()
        text = _call_notify("notify_client_preparing", o, r)
        assert "prepared" in text
        assert str(o.id) in text

    def test_unknown_language_fallback(self):
        r = _make_restaurant(language="xx")
        o = _make_order()
        text = _call_notify("notify_client_preparing", o, r)
        assert text
        assert str(o.id) in text


# ══════════════════════════════════════════
# ГРУППА 3: notify_client_ready
# ══════════════════════════════════════════

class TestNotifyClientReady:

    def test_uz_delivery(self):
        r = _make_restaurant(language="uz")
        o = _make_order(order_type="delivery")
        text = _call_notify("notify_client_ready", o, r)
        assert "tayyor" in text.lower()
        assert "Kuryer" in text
        assert str(o.id) in text

    def test_uz_takeaway(self):
        r = _make_restaurant(language="uz")
        o = _make_order(order_type="takeaway")
        text = _call_notify("notify_client_ready", o, r)
        assert "Olib ketishingiz" in text

    def test_uz_dine_in(self):
        r = _make_restaurant(language="uz")
        o = _make_order(order_type="dine_in")
        text = _call_notify("notify_client_ready", o, r)
        assert "Stolingizga" in text

    def test_ru_delivery(self):
        r = _make_restaurant(language="ru")
        o = _make_order(order_type="delivery")
        text = _call_notify("notify_client_ready", o, r)
        assert "готов" in text
        assert "Курьер" in text

    def test_ru_takeaway(self):
        r = _make_restaurant(language="ru")
        o = _make_order(order_type="takeaway")
        text = _call_notify("notify_client_ready", o, r)
        assert "забирать" in text

    def test_en_delivery(self):
        r = _make_restaurant(language="en")
        o = _make_order(order_type="delivery")
        text = _call_notify("notify_client_ready", o, r)
        assert "ready" in text.lower()
        assert "courier" in text.lower()

    def test_unknown_order_type_fallback(self):
        """Неизвестный order_type → detail fallback, не крашится."""
        r = _make_restaurant(language="uz")
        o = _make_order(order_type="future_type")
        text = _call_notify("notify_client_ready", o, r)
        assert text

    def test_unknown_language_fallback(self):
        r = _make_restaurant(language="de")
        o = _make_order(order_type="delivery")
        text = _call_notify("notify_client_ready", o, r)
        assert text
        assert str(o.id) in text


# ══════════════════════════════════════════
# ГРУППА 4: notify_client_delivering
# ══════════════════════════════════════════

class TestNotifyClientDelivering:

    def test_uz_language(self):
        r = _make_restaurant(language="uz")
        o = _make_order()
        text = _call_notify("notify_client_delivering", o, r)
        assert "Kuryer" in text
        assert "yo'lda" in text
        assert str(o.id) in text

    def test_ru_language(self):
        r = _make_restaurant(language="ru")
        o = _make_order()
        text = _call_notify("notify_client_delivering", o, r)
        assert "Курьер" in text
        assert str(o.id) in text

    def test_en_language(self):
        r = _make_restaurant(language="en")
        o = _make_order()
        text = _call_notify("notify_client_delivering", o, r)
        assert "courier" in text.lower() or "way" in text.lower()
        assert str(o.id) in text

    def test_unknown_language_fallback(self):
        r = _make_restaurant(language="zz")
        o = _make_order()
        text = _call_notify("notify_client_delivering", o, r)
        assert text
        assert str(o.id) in text


# ══════════════════════════════════════════
# ГРУППА 5: notify_client_completed
# ══════════════════════════════════════════

class TestNotifyClientCompleted:

    def test_uz_language(self):
        r = _make_restaurant(language="uz")
        o = _make_order()
        text = _call_notify("notify_client_completed", o, r)
        assert "yetkazildi" in text
        assert "Rahmat" in text
        assert str(o.id) in text

    def test_ru_language(self):
        r = _make_restaurant(language="ru")
        o = _make_order()
        text = _call_notify("notify_client_completed", o, r)
        assert "доставлен" in text
        assert "Спасибо" in text
        assert str(o.id) in text

    def test_en_language(self):
        r = _make_restaurant(language="en")
        o = _make_order()
        text = _call_notify("notify_client_completed", o, r)
        assert "delivered" in text.lower() or "Thank" in text
        assert str(o.id) in text

    def test_unknown_language_fallback(self):
        r = _make_restaurant(language="qq")
        o = _make_order()
        text = _call_notify("notify_client_completed", o, r)
        assert text
        assert str(o.id) in text


# ══════════════════════════════════════════
# ГРУППА 6: notify_client_cancelled
# ══════════════════════════════════════════

class TestNotifyClientCancelled:

    def test_uz_no_comment(self):
        r = _make_restaurant(language="uz")
        o = _make_order()
        text = _call_notify("notify_client_cancelled", o, r)
        assert "bekor qilindi" in text
        assert "Uzr" in text
        assert str(o.id) in text
        assert "Sabab:" not in text  # нет причины

    def test_uz_with_comment(self):
        r = _make_restaurant(language="uz")
        o = _make_order()
        text = _call_notify("notify_client_cancelled", o, r, comment="Mahsulot tugadi")
        assert "Sabab:" in text
        assert "Mahsulot tugadi" in text

    def test_ru_no_comment(self):
        r = _make_restaurant(language="ru")
        o = _make_order()
        text = _call_notify("notify_client_cancelled", o, r)
        assert "отменён" in text
        assert "Причина:" not in text

    def test_ru_with_comment(self):
        r = _make_restaurant(language="ru")
        o = _make_order()
        text = _call_notify("notify_client_cancelled", o, r, comment="Нет в наличии")
        assert "Причина:" in text
        assert "Нет в наличии" in text

    def test_en_no_comment(self):
        r = _make_restaurant(language="en")
        o = _make_order()
        text = _call_notify("notify_client_cancelled", o, r)
        assert "cancelled" in text.lower()
        assert "Reason:" not in text

    def test_en_with_comment(self):
        r = _make_restaurant(language="en")
        o = _make_order()
        text = _call_notify("notify_client_cancelled", o, r, comment="Out of stock")
        assert "Reason:" in text
        assert "Out of stock" in text

    def test_empty_comment_no_reason_line(self):
        """Пустой comment → reason не добавляется."""
        r = _make_restaurant(language="uz")
        o = _make_order()
        text = _call_notify("notify_client_cancelled", o, r, comment="   ")
        assert "Sabab:" not in text

    def test_unknown_language_fallback(self):
        r = _make_restaurant(language="unknown")
        o = _make_order()
        text = _call_notify("notify_client_cancelled", o, r)
        assert text
        assert str(o.id) in text


# ══════════════════════════════════════════
# ГРУППА 7: бизнес-логика не сломана
# ══════════════════════════════════════════

class TestBusinessLogicIntact:

    def test_client_telegram_id_missing_no_crash(self):
        """Если нет client_telegram_id — уведомление не отправляется, не крашится."""
        r = _make_restaurant(language="uz")
        o = _make_order(client_telegram_id=None)

        sent = {}

        def fake_bot_send(chat_id, text):
            sent["called"] = True

        with patch("handlers._get_bot", return_value=MagicMock(send_message=fake_bot_send)):
            import handlers
            # _notify_client сам проверяет client_telegram_id
            handlers.notify_client_accepted(o, r)

        assert "called" not in sent  # ничего не отправлено

    def test_all_six_functions_callable(self):
        """Все 6 notify_client_* функций вызываются без исключений."""
        import handlers
        r = _make_restaurant(language="uz")
        o = _make_order()

        funcs = [
            ("notify_client_accepted",   (o, r)),
            ("notify_client_preparing",  (o, r)),
            ("notify_client_ready",      (o, r)),
            ("notify_client_delivering", (o, r)),
            ("notify_client_completed",  (o, r)),
            ("notify_client_cancelled",  (o, r)),
        ]

        with patch("handlers._notify_client"):
            for func_name, args in funcs:
                getattr(handlers, func_name)(*args)  # не должно бросать

    def test_order_id_present_in_all_notifications(self):
        """ID заказа присутствует в тексте каждого уведомления для всех языков."""
        r_list = [
            _make_restaurant(language="uz"),
            _make_restaurant(language="ru"),
            _make_restaurant(language="en"),
        ]
        func_names = [
            "notify_client_accepted",
            "notify_client_preparing",
            "notify_client_ready",
            "notify_client_delivering",
            "notify_client_completed",
            "notify_client_cancelled",
        ]
        o = _make_order(order_id=777)

        for r in r_list:
            for func_name in func_names:
                text = _call_notify(func_name, o, r)
                assert "777" in text, (
                    f"{func_name} [{r.language}]: order id '777' не найден в тексте: {text!r}"
                )

    def test_no_hardcoded_uz_strings_in_ru_notification(self):
        """RU ресторан не должен получать UZ строки в уведомлениях."""
        r = _make_restaurant(language="ru")
        o = _make_order()
        uz_markers = ["Buyurtma", "qabul qilindi", "Rahmat", "Uzr"]

        for func_name in ["notify_client_accepted", "notify_client_preparing",
                          "notify_client_ready", "notify_client_delivering",
                          "notify_client_completed", "notify_client_cancelled"]:
            text = _call_notify(func_name, o, r)
            for marker in uz_markers:
                assert marker not in text, (
                    f"{func_name}: UZ строка {marker!r} найдена в RU уведомлении: {text!r}"
                )

    def test_no_hardcoded_uz_strings_in_en_notification(self):
        """EN ресторан не должен получать UZ строки."""
        r = _make_restaurant(language="en")
        o = _make_order()
        uz_markers = ["Buyurtma", "qabul qilindi", "Rahmat", "Uzr"]

        for func_name in ["notify_client_accepted", "notify_client_preparing",
                          "notify_client_ready", "notify_client_delivering",
                          "notify_client_completed", "notify_client_cancelled"]:
            text = _call_notify(func_name, o, r)
            for marker in uz_markers:
                assert marker not in text, (
                    f"{func_name}: UZ строка {marker!r} найдена в EN уведомлении: {text!r}"
                )
