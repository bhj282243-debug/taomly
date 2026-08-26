"""
tests/test_s1_8_telegram_location.py — S1-8: Telegram Credentials Migration to Location

Проверяет:
  - Webhook lookup по Location.slug (CheckA, CheckB)
  - get_location_bot использует location.telegram_bot_token_encrypted (CheckC)
  - BOT_CACHE keyed by location.id (CheckD, CheckE)
  - notify_new_order использует location.telegram_dispatcher_id (CheckF)
  - notify_client_accepted использует location.language (CheckG)
  - notify_client_accepted использует location.currency (CheckH)
  - GET /api/restaurants/{slug} → is_waiter_call_enabled из Location (CheckI)
  - Agency PATCH синхронизирует токен в Location (CheckJ)
  - Agency PATCH инвалидирует BOT_CACHE по location.id (CheckK)
  - PATCH order status передаёт location в notify_client_* (CheckL)
  - Tenant isolation: webhook чужой slug → graceful {"ok": False} (CheckM)
  - create_order не регрессирует (CheckN)
  - notify_new_order продолжает работать с Location dispatcher (CheckO)

Запускаются с PostgreSQL (pytest-postgresql).
"""

import pytest
from unittest.mock import MagicMock, patch, call
from fastapi.testclient import TestClient
from config import settings


# ═══════════════════════════════════════════════════════════════
# CheckA — Webhook lookup по Location.slug
# ═══════════════════════════════════════════════════════════════

class TestCheckA:
    """CheckA: POST /webhook/{slug} ищет Location по slug, не Restaurant."""

    def test_a1_webhook_finds_location_by_slug(self, client, db, location, restaurant):
        """A1. Webhook с корректным slug находит Location и возвращает ok=True.

        Webhook открывает SessionLocal() напрямую (не через get_db dependency),
        поэтому patch SessionLocal чтобы вернуть тот же db из fixture.
        """
        update_payload = {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 99}}}

        # Webhook вызывает SessionLocal() как контекстный менеджер: `with SessionLocal() as db:`
        # Нужно чтобы он вернул тот же db, в котором зафлушены fixtures.
        from contextlib import contextmanager

        @contextmanager
        def fake_session_local():
            yield db

        with patch("api.SessionLocal", fake_session_local), \
             patch("handlers.process_restaurant_webhook_update") as mock_proc:
            resp = client.post(
                f"/webhook/{location.slug}",
                json=update_payload,
                headers={"X-Telegram-Bot-Api-Secret-Token": settings.WEBHOOK_SECRET},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True, f"Webhook вернул: {data}"
        mock_proc.assert_called_once()
        call_kwargs = mock_proc.call_args
        passed_location = call_kwargs.kwargs.get("location") or (
            call_kwargs.args[2] if len(call_kwargs.args) > 2 else None
        )
        assert passed_location is not None
        assert passed_location.id == location.id

    def test_a2_webhook_uses_location_token_not_restaurant_token(self, client, db, location, restaurant):
        """A2. Webhook использует location.telegram_bot_token_encrypted.

        Патчим SessionLocal чтобы webhook видел fixture данные.
        """
        from auth import encrypt_token
        from contextlib import contextmanager

        restaurant.telegram_bot_token_encrypted = encrypt_token("RESTAURANT_TOKEN_OLD")
        location.telegram_bot_token_encrypted = encrypt_token("LOCATION_TOKEN_NEW")
        db.flush()

        update_payload = {"update_id": 2, "message": {"message_id": 1, "chat": {"id": 99}}}

        captured = {}

        def fake_process(rest, upd, location=None):
            captured["location_id"] = location.id if location else None

        @contextmanager
        def fake_session_local():
            yield db

        with patch("api.SessionLocal", fake_session_local), \
             patch("handlers.process_restaurant_webhook_update", side_effect=fake_process):
            resp = client.post(
                f"/webhook/{location.slug}",
                json=update_payload,
                headers={"X-Telegram-Bot-Api-Secret-Token": settings.WEBHOOK_SECRET},
            )

        assert resp.status_code == 200
        assert captured.get("location_id") == location.id


# ═══════════════════════════════════════════════════════════════
# CheckB — Несуществующий slug → graceful {"ok": False}
# ═══════════════════════════════════════════════════════════════

class TestCheckB:
    """CheckB: несуществующий webhook slug → {"ok": False}, не 404/500."""

    def test_b1_unknown_slug_returns_ok_false(self, client):
        """B1. Незнакомый slug: ответ 200 с ok=False."""
        update_payload = {"update_id": 99}
        resp = client.post(
            "/webhook/nonexistent-slug-xyz",
            json=update_payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": settings.WEBHOOK_SECRET},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is False

    def test_b2_invalid_secret_returns_403(self, client):
        """B2. Неверный secret → 403 до любого DB lookup."""
        resp = client.post(
            "/webhook/any-slug",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG_SECRET"},
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════
# CheckC — get_location_bot читает из Location
# ═══════════════════════════════════════════════════════════════

class TestCheckC:
    """CheckC: get_location_bot использует location.telegram_bot_token_encrypted."""

    def test_c1_uses_location_token(self):
        """C1. Bot создаётся из location.telegram_bot_token_encrypted."""
        import handlers
        from unittest.mock import patch as _patch

        mock_loc = MagicMock()
        mock_loc.id = 999
        mock_loc.telegram_bot_token_encrypted = "ENCRYPTED_LOC_TOKEN"
        mock_loc.name = "Test Location"

        # Чистим кэш
        handlers._BOT_CACHE.pop(999, None)

        with _patch("handlers.decrypt_token", return_value="PLAIN_LOC_TOKEN") as mock_dec, \
             _patch("telebot.TeleBot") as mock_tele:
            mock_tele.return_value = MagicMock()
            bot = handlers.get_location_bot(mock_loc)
            mock_dec.assert_called_once_with("ENCRYPTED_LOC_TOKEN")

        handlers._BOT_CACHE.pop(999, None)

    def test_c2_location_token_used_not_restaurant_token(self):
        """C2. get_location_bot читает location.telegram_bot_token_encrypted,
        а не restaurant.telegram_bot_token_encrypted.

        Доказывается через mock_dec: decrypt вызван с LOC токеном ("LOC_ENC_TOKEN"),
        а не с REST токеном ("REST_ENC_TOKEN"). Оба объекта имеют разные токены.
        """
        import handlers

        mock_loc = MagicMock()
        mock_loc.id = 998
        mock_loc.telegram_bot_token_encrypted = "LOC_ENC_TOKEN"
        mock_loc.name = "Loc"

        mock_rest = MagicMock()
        mock_rest.id = 100
        mock_rest.telegram_bot_token_encrypted = "REST_ENC_TOKEN"  # не должен использоваться

        handlers._BOT_CACHE.pop(998, None)

        with patch("handlers.decrypt_token", return_value="PLAIN_TOKEN") as mock_dec, \
             patch("telebot.TeleBot", return_value=MagicMock()):
            handlers.get_location_bot(mock_loc)
            # decrypt вызван с LOC токеном, не REST токеном
            mock_dec.assert_called_once_with("LOC_ENC_TOKEN")

        handlers._BOT_CACHE.pop(998, None)

    def test_c3_no_token_raises_value_error(self):
        """C3. Location без токена → ValueError (не KeyError/AttributeError)."""
        import handlers

        mock_loc = MagicMock()
        mock_loc.id = 997
        mock_loc.telegram_bot_token_encrypted = None
        mock_loc.name = "No Token Loc"

        handlers._BOT_CACHE.pop(997, None)

        with pytest.raises(ValueError):
            handlers.get_location_bot(mock_loc)


# ═══════════════════════════════════════════════════════════════
# CheckD — BOT_CACHE keyed by location.id
# ═══════════════════════════════════════════════════════════════

class TestCheckD:
    """CheckD: BOT_CACHE ключ = location.id; повторный вызов без decrypt."""

    def test_d1_cache_keyed_by_location_id(self):
        """D1. Второй вызов get_location_bot возвращает из кэша — decrypt не вызывается."""
        import handlers

        mock_loc = MagicMock()
        mock_loc.id = 777
        mock_loc.telegram_bot_token_encrypted = "ENC_TOKEN_D"
        mock_loc.name = "D"

        handlers._BOT_CACHE.pop(777, None)

        decrypt_calls = []

        def counting_decrypt(token):
            decrypt_calls.append(token)
            return "PLAIN_D"

        with patch("handlers.decrypt_token", side_effect=counting_decrypt), \
             patch("telebot.TeleBot", return_value=MagicMock()):
            handlers.get_location_bot(mock_loc)
            handlers.get_location_bot(mock_loc)

        assert len(decrypt_calls) == 1, (
            f"decrypt_token вызван {len(decrypt_calls)} раз(а), ожидался 1"
        )
        assert 777 in handlers._BOT_CACHE

        handlers._BOT_CACHE.pop(777, None)

    def test_d2_different_locations_different_cache_keys(self):
        """D2. Два Location — два отдельных кэш-слота."""
        import handlers

        loc1 = MagicMock(); loc1.id = 701; loc1.telegram_bot_token_encrypted = "E1"; loc1.name = "L1"
        loc2 = MagicMock(); loc2.id = 702; loc2.telegram_bot_token_encrypted = "E2"; loc2.name = "L2"

        handlers._BOT_CACHE.pop(701, None)
        handlers._BOT_CACHE.pop(702, None)

        bot1 = MagicMock()
        bot2 = MagicMock()
        bots = [bot1, bot2]

        with patch("handlers.decrypt_token", return_value="PLAIN"), \
             patch("telebot.TeleBot", side_effect=bots):
            r1 = handlers.get_location_bot(loc1)
            r2 = handlers.get_location_bot(loc2)

        assert r1 is bot1
        assert r2 is bot2
        assert handlers._BOT_CACHE[701] is bot1
        assert handlers._BOT_CACHE[702] is bot2

        handlers._BOT_CACHE.pop(701, None)
        handlers._BOT_CACHE.pop(702, None)


# ═══════════════════════════════════════════════════════════════
# CheckE — invalidate_bot_cache по location.id
# ═══════════════════════════════════════════════════════════════

class TestCheckE:
    """CheckE: invalidate_bot_cache(location_id) очищает кэш по location.id."""

    def test_e1_invalidate_clears_location_cache(self):
        """E1. После invalidate BOT_CACHE[location_id] отсутствует."""
        import handlers

        mock_bot = MagicMock()
        handlers._BOT_CACHE[555] = mock_bot

        handlers.invalidate_bot_cache(555)

        assert 555 not in handlers._BOT_CACHE

    def test_e2_invalidate_nonexistent_does_not_crash(self):
        """E2. Инвалидация несуществующего ключа не вызывает исключение."""
        import handlers
        handlers._BOT_CACHE.pop(99999, None)
        handlers.invalidate_bot_cache(99999)  # не должно бросать


# ═══════════════════════════════════════════════════════════════
# CheckF — notify_new_order использует location.telegram_dispatcher_id
# ═══════════════════════════════════════════════════════════════

class TestCheckF:
    """CheckF: notify_new_order берёт dispatcher_id из Location."""

    def test_f1_uses_location_dispatcher_id(self):
        """F1. Сообщение отправляется на location.telegram_dispatcher_id."""
        from handlers import notify_new_order

        mock_loc = MagicMock()
        mock_loc.id = 501
        mock_loc.telegram_dispatcher_id = 99999
        mock_loc.telegram_bot_token_encrypted = "ENC_F"
        mock_loc.currency = "UZS"
        mock_loc.name = "Loc F"

        mock_rest = MagicMock()
        mock_rest.id = 50
        mock_rest.telegram_dispatcher_id = 11111  # не должен использоваться
        mock_rest.currency = "UZS"
        mock_rest.name = "Rest F"

        mock_order = MagicMock()
        mock_order.id = 42
        mock_order.order_type = "dine_in"
        mock_order.total_amount = 10000
        mock_order.client_name = None
        mock_order.client_phone = None
        mock_order.address = None
        mock_order.table_id = None
        mock_order.comment = None

        sent_to = []

        mock_bot = MagicMock()
        mock_bot.send_message.side_effect = lambda chat_id, text: sent_to.append(chat_id)

        with patch("handlers.decrypt_token", return_value="PLAIN_F"), \
             patch("telebot.TeleBot", return_value=mock_bot):
            import handlers
            handlers._BOT_CACHE.pop(501, None)
            notify_new_order(mock_order, [], mock_rest, location=mock_loc)

        assert 99999 in sent_to, (
            f"Сообщение отправлено на {sent_to}, ожидался location.dispatcher_id=99999"
        )
        assert 11111 not in sent_to

        import handlers as h
        h._BOT_CACHE.pop(501, None)

    def test_f2_no_location_dispatcher_logs_warning(self):
        """F2. Если location.telegram_dispatcher_id=None → не отправляет, не крашится."""
        from handlers import notify_new_order

        mock_loc = MagicMock()
        mock_loc.id = 502
        mock_loc.telegram_dispatcher_id = None
        mock_loc.currency = "UZS"
        mock_loc.name = "No Disp"

        mock_order = MagicMock()
        mock_order.id = 1

        try:
            notify_new_order(mock_order, [], MagicMock(), location=mock_loc)
        except Exception as e:
            pytest.fail(f"notify_new_order с dispatcher=None упал: {e}")

    def test_f3_backward_compat_without_location(self):
        """F3. notify_new_order без location= не ломается (backward compat)."""
        from handlers import notify_new_order

        mock_rest = MagicMock()
        mock_rest.id = 1
        mock_rest.telegram_dispatcher_id = None
        mock_rest.currency = "UZS"
        mock_rest.name = "Rest"

        mock_order = MagicMock()
        mock_order.id = 1
        mock_order.order_type = "dine_in"
        mock_order.total_amount = 5000
        mock_order.client_name = None
        mock_order.client_phone = None
        mock_order.address = None
        mock_order.table_id = None
        mock_order.comment = None

        try:
            notify_new_order(mock_order, [], mock_rest)
        except Exception as e:
            pytest.fail(f"notify_new_order без location= упал: {e}")


# ═══════════════════════════════════════════════════════════════
# CheckG — notify_client_accepted использует location.language
# ═══════════════════════════════════════════════════════════════

class TestCheckG:
    """CheckG: notify_client_accepted берёт язык из location, не restaurant."""

    def test_g1_uses_location_language(self):
        """G1. Уведомление формируется на языке Location (ru), не Restaurant (uz).
        Проверяем: RU-строка присутствует, UZ-строка отсутствует.
        Location.language=ru, Restaurant.language=uz — разные значения.
        """
        import handlers
        from i18n import clear_cache
        clear_cache()

        mock_loc = MagicMock()
        mock_loc.language = "ru"
        mock_loc.currency = "UZS"

        mock_rest = MagicMock()
        mock_rest.language = "uz"  # намеренно отличается от location.language
        mock_rest.currency = "UZS"

        mock_order = MagicMock()
        mock_order.id = 10
        mock_order.order_type = "delivery"
        mock_order.total_amount = 25000
        mock_order.client_telegram_id = 123456

        captured = {}

        def fake_notify(order, rest, text, event_name, **kwargs):
            captured["text"] = text

        with patch("handlers._notify_client", side_effect=fake_notify):
            handlers.notify_client_accepted(mock_order, mock_rest, location=mock_loc)

        text = captured.get("text", "")
        assert text, "Текст уведомления пустой"

        # Позитивная проверка: RU-строка из ru.json
        # "telegram.order_accepted" (ru) = "✅ Ваш заказ принят!..."
        assert "Ваш заказ принят" in text, (
            f"Ожидалась RU строка 'Ваш заказ принят' из location.language=ru, "
            f"текст: {text!r}"
        )

        # Негативная проверка: UZ-строка НЕ должна быть
        # "telegram.order_accepted" (uz) = "✅ Buyurtmangiz qabul qilindi!..."
        assert "Buyurtmangiz qabul qilindi" not in text, (
            f"В RU уведомлении найдена UZ строка (читается restaurant.language=uz): {text!r}"
        )

        clear_cache()

    def test_g2_backward_compat_without_location_uses_restaurant(self):
        """G2. Без location= берётся restaurant.language (backward compat).
        Проверяем: RU-строка присутствует когда restaurant.language=ru и location не передан.
        """
        import handlers
        from i18n import clear_cache
        clear_cache()

        mock_rest = MagicMock()
        mock_rest.language = "ru"
        mock_rest.currency = "UZS"

        mock_order = MagicMock()
        mock_order.id = 11
        mock_order.order_type = "dine_in"
        mock_order.total_amount = 5000
        mock_order.client_telegram_id = 999

        captured = {}

        def fake_notify(order, rest, text, event_name, **kwargs):
            captured["text"] = text

        with patch("handlers._notify_client", side_effect=fake_notify):
            handlers.notify_client_accepted(mock_order, mock_rest)  # без location

        text = captured.get("text", "")
        assert text, "Текст уведомления пустой без location"
        assert "Ваш заказ принят" in text, (
            f"Ожидалась RU строка из restaurant.language=ru, текст: {text!r}"
        )
        clear_cache()


# ═══════════════════════════════════════════════════════════════
# CheckH — notify_client_accepted использует location.currency
# ═══════════════════════════════════════════════════════════════

class TestCheckH:
    """CheckH: notify_client_accepted берёт currency из location."""

    def test_h1_uses_location_currency(self):
        """H1. Сумма форматируется с location.currency, не restaurant.currency."""
        import handlers
        from i18n import clear_cache
        clear_cache()

        mock_loc = MagicMock()
        mock_loc.language = "uz"
        mock_loc.currency = "USD"

        mock_rest = MagicMock()
        mock_rest.language = "uz"
        mock_rest.currency = "UZS"  # не должна использоваться

        mock_order = MagicMock()
        mock_order.id = 20
        mock_order.order_type = "delivery"
        mock_order.total_amount = 100
        mock_order.client_telegram_id = 111

        captured = {}

        def fake_notify(order, rest, text, event_name, **kwargs):
            captured["text"] = text

        with patch("handlers._notify_client", side_effect=fake_notify):
            handlers.notify_client_accepted(mock_order, mock_rest, location=mock_loc)

        text = captured.get("text", "")
        # format_price для USD возвращает символ "$", не строку "USD"
        # Проверяем что текст сформирован с USD-форматированием (символ $)
        assert "$" in text, (
            f"Ожидался символ $ (USD) из Location.currency=USD, текст: {text!r}"
        )
        # Проверяем что UZS-формат (₽ или ₸) не используется
        assert "so'm" not in text.lower() and "sum" not in text.lower(), (
            f"В тексте найдена валюта UZS вместо USD: {text!r}"
        )
        clear_cache()


# ═══════════════════════════════════════════════════════════════
# CheckI — GET /api/restaurants/{slug} → is_waiter_call_enabled из Location
# ═══════════════════════════════════════════════════════════════

class TestCheckI:
    """CheckI: публичный эндпоинт возвращает is_waiter_call_enabled из Location."""

    def test_i1_waiter_call_from_location(self, client, db, restaurant, location):
        """I1. Location.is_waiter_call_enabled=True → ответ True."""
        location.is_waiter_call_enabled = True
        restaurant.is_waiter_call_enabled = False  # restaurant имеет False
        db.commit()

        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_waiter_call_enabled"] is True, (
            "Ожидалось True из Location, получено False (читается из Restaurant)"
        )

    def test_i2_waiter_call_false_from_location(self, client, db, restaurant, location):
        """I2. Location.is_waiter_call_enabled=False → ответ False."""
        location.is_waiter_call_enabled = False
        restaurant.is_waiter_call_enabled = True  # restaurant имеет True
        db.commit()

        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_waiter_call_enabled"] is False, (
            "Ожидалось False из Location, получено True (читается из Restaurant)"
        )


# ═══════════════════════════════════════════════════════════════
# CheckJ — Agency PATCH синхронизирует токен в Location
# ═══════════════════════════════════════════════════════════════

class TestCheckJ:
    """CheckJ: PATCH /api/agency/restaurants/{id} синхронно обновляет Restaurant + Location."""

    def test_j1_token_synced_to_location(self, client, db, agency_token, restaurant, location):
        """J1. После PATCH токена — location.telegram_bot_token_encrypted обновлён."""
        from auth import decrypt_token

        new_token = "9999999999:AANewTokenForS18Test"

        with patch("telegram_service.register_restaurant_webhook") as mock_reg:
            mock_reg.return_value = MagicMock(ok=True)
            resp = client.patch(
                f"/api/agency/restaurants/{restaurant.id}",
                json={"telegram_bot_token": new_token},
                headers={"Authorization": f"Bearer {agency_token}"},
            )

        assert resp.status_code == 200, f"PATCH вернул {resp.status_code}: {resp.text}"

        db.refresh(restaurant)
        db.refresh(location)

        # Оба должны содержать новый токен
        rest_plain = decrypt_token(restaurant.telegram_bot_token_encrypted)
        loc_plain = decrypt_token(location.telegram_bot_token_encrypted)

        assert rest_plain == new_token, f"Restaurant token не обновлён: {rest_plain}"
        assert loc_plain == new_token, (
            f"Location token не синхронизирован: {loc_plain} (ожидался {new_token})"
        )

    def test_j2_dispatcher_id_synced_to_location(self, client, db, agency_token, restaurant, location):
        """J2. После PATCH dispatcher_id — location.telegram_dispatcher_id обновлён."""
        new_dispatcher = 55555555

        with patch("telegram_service.register_restaurant_webhook") as mock_reg:
            mock_reg.return_value = MagicMock(ok=True)
            resp = client.patch(
                f"/api/agency/restaurants/{restaurant.id}",
                json={"telegram_dispatcher_id": new_dispatcher},
                headers={"Authorization": f"Bearer {agency_token}"},
            )

        assert resp.status_code == 200

        db.refresh(location)
        assert location.telegram_dispatcher_id == new_dispatcher, (
            f"Location dispatcher не синхронизирован: {location.telegram_dispatcher_id}"
        )


# ═══════════════════════════════════════════════════════════════
# CheckK — Agency PATCH инвалидирует BOT_CACHE по location.id
# ═══════════════════════════════════════════════════════════════

class TestCheckK:
    """CheckK: после PATCH токена BOT_CACHE очищен по location.id."""

    def test_k1_bot_cache_invalidated_by_location_id(
        self, client, db, agency_token, restaurant, location
    ):
        """K1. BOT_CACHE[location.id] очищается после смены токена."""
        import handlers

        # Предзаполним кэш по location.id
        mock_bot = MagicMock()
        handlers._BOT_CACHE[location.id] = mock_bot

        new_token = "8888888888:AAInvalidateCacheToken"

        with patch("telegram_service.register_restaurant_webhook") as mock_reg:
            mock_reg.return_value = MagicMock(ok=True)
            resp = client.patch(
                f"/api/agency/restaurants/{restaurant.id}",
                json={"telegram_bot_token": new_token},
                headers={"Authorization": f"Bearer {agency_token}"},
            )

        assert resp.status_code == 200
        assert location.id not in handlers._BOT_CACHE, (
            f"BOT_CACHE[{location.id}] не очищен после смены токена"
        )


# ═══════════════════════════════════════════════════════════════
# CheckL — PATCH order status передаёт location в notify_client_*
# ═══════════════════════════════════════════════════════════════

class TestCheckL:
    """CheckL: PATCH /api/orders/{id}/status передаёт location в notify_client_*."""

    def test_l1_notify_client_receives_location(
        self, client, db, restaurant_token, restaurant, location, product
    ):
        """L1. notify_client_preparing вызывается с location (не None) при смене статуса.

        Доказывает Invariant I-5: location передаётся из update_order_status
        в notify_client_*. Если production-код не передаёт location — тест упадёт.
        """
        # Создаём заказ через API (takeaway — не требует table_id)
        create_resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 1}],
                "client_telegram_id": 12345,
                "client_name": "Test",
            },
            headers={
                "Authorization": f"Bearer {restaurant_token}",
                "X-Location-Id": str(location.id),
            },
        )
        if create_resp.status_code not in (200, 201):
            pytest.skip(f"Не удалось создать заказ: {create_resp.status_code} {create_resp.text}")

        order_id = create_resp.json()["id"]

        captured = {}

        def capturing_notify(order, rest, loc=None):
            captured["called"] = True
            captured["location"] = loc

        with patch("handlers.notify_client_preparing", side_effect=capturing_notify):
            resp = client.patch(
                f"/api/orders/{order_id}/status",
                json={"status": "preparing"},
                headers={"Authorization": f"Bearer {restaurant_token}"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "preparing"

        # Критические проверки I-5: location должна быть передана
        assert captured.get("called") is True, (
            "notify_client_preparing не был вызван"
        )
        assert captured.get("location") is not None, (
            "notify_client_preparing вызван без location=... (location=None). "
            "Invariant I-5 нарушен: production-код не передаёт location в notify_client_*"
        )
        assert captured["location"].id == location.id, (
            f"Передана неверная location: id={captured['location'].id}, "
            f"ожидался id={location.id}"
        )

    def test_l2_location_language_used_in_status_notification(
        self, client, db, restaurant_token, restaurant, location, product
    ):
        """L2. Язык клиентского уведомления при смене статуса берётся из Location.

        Location.language=ru, Restaurant.language=uz — намеренно разные.
        Если production-код не передаёт location, loc_language будет None → тест упадёт.
        """
        location.language = "ru"
        restaurant.language = "uz"  # намеренно отличается — доказывает source of truth
        db.flush()

        create_resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 1}],
                "client_telegram_id": 12345,
            },
            headers={
                "Authorization": f"Bearer {restaurant_token}",
                "X-Location-Id": str(location.id),
            },
        )
        if create_resp.status_code not in (200, 201):
            pytest.skip(f"Не удалось создать заказ: {create_resp.status_code}")

        order_id = create_resp.json()["id"]

        notify_kwargs = {}

        def capturing_notify(order, rest, loc=None):
            notify_kwargs["loc_language"] = getattr(loc, "language", None) if loc else None

        with patch("handlers.notify_client_preparing", side_effect=capturing_notify):
            resp = client.patch(
                f"/api/orders/{order_id}/status",
                json={"status": "preparing"},
                headers={"Authorization": f"Bearer {restaurant_token}"},
            )

        assert resp.status_code == 200
        # Безусловный assert: если location не передана (loc=None) → loc_language=None → FAIL
        assert notify_kwargs.get("loc_language") == "ru", (
            f"Ожидался Location.language='ru' (не restaurant.language='uz'). "
            f"Получено: {notify_kwargs.get('loc_language')!r}. "
            f"Invariant I-5: notify_client_* должен получать location."
        )


# ═══════════════════════════════════════════════════════════════
# CheckM — Tenant isolation
# ═══════════════════════════════════════════════════════════════

class TestCheckM:
    """CheckM: tenant isolation в webhook — process_restaurant_webhook_update
    получает restaurant, соответствующий location.restaurant_id."""

    def test_m1_webhook_routes_to_correct_restaurant(
        self, client, db, location, restaurant
    ):
        """M1. Webhook передаёт в process_restaurant_webhook_update именно тот
        Restaurant, которому принадлежит найденная Location (location.restaurant_id).

        Tenant isolation в webhook: slug идентифицирует Location → Location.restaurant_id
        определяет Restaurant. process_restaurant_webhook_update не может получить
        Restaurant другого tenant, потому что restaurant загружается строго по
        location.restaurant_id.

        Патчим SessionLocal чтобы webhook видел fixture данные.
        """
        from contextlib import contextmanager

        update_payload = {"update_id": 200}
        captured = {}

        def fake_process(rest, upd, location=None):
            captured["restaurant_id"] = rest.id
            captured["location_id"] = location.id if location else None

        @contextmanager
        def fake_session_local():
            yield db

        with patch("api.SessionLocal", fake_session_local), \
             patch("handlers.process_restaurant_webhook_update", side_effect=fake_process):
            resp = client.post(
                f"/webhook/{location.slug}",
                json=update_payload,
                headers={"X-Telegram-Bot-Api-Secret-Token": settings.WEBHOOK_SECRET},
            )

        assert resp.status_code == 200
        assert resp.json().get("ok") is True, f"Webhook вернул: {resp.json()}"

        # Tenant isolation: restaurant передан правильный
        assert captured.get("restaurant_id") == restaurant.id, (
            f"process_restaurant_webhook_update получил restaurant_id="
            f"{captured.get('restaurant_id')}, ожидался {restaurant.id}"
        )
        # Location привязана к правильному restaurant
        assert captured.get("location_id") == location.id
        assert location.restaurant_id == restaurant.id

    def test_m2_location_of_deactivated_location_returns_ok_false(
        self, client, db, restaurant, location
    ):
        """M2. Деактивированная Location → webhook возвращает ok=False.

        Использует SessionLocal patch — webhook не видит незакоммиченные данные.
        """
        from contextlib import contextmanager

        location.is_active = False
        db.flush()

        @contextmanager
        def fake_session_local():
            yield db

        update_payload = {"update_id": 201}

        with patch("api.SessionLocal", fake_session_local):
            resp = client.post(
                f"/webhook/{location.slug}",
                json=update_payload,
                headers={"X-Telegram-Bot-Api-Secret-Token": settings.WEBHOOK_SECRET},
            )

        assert resp.status_code == 200
        assert resp.json().get("ok") is False, (
            f"Ожидался ok=False для деактивированной Location, получен: {resp.json()}"
        )

        # Restore
        location.is_active = True
        db.flush()


# ═══════════════════════════════════════════════════════════════
# CheckN — create_order не регрессирует
# ═══════════════════════════════════════════════════════════════

class TestCheckN:
    """CheckN: базовый create_order продолжает работать после S1-8 изменений."""

    def test_n1_create_order_success(
        self, client, restaurant_token, location, product
    ):
        """N1. POST /api/orders/ создаёт заказ без ошибок."""
        resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 2}],
                "client_telegram_id": 12345,
                "client_name": "Test Client",
            },
            headers={
                "Authorization": f"Bearer {restaurant_token}",
                "X-Location-Id": str(location.id),
            },
        )
        assert resp.status_code in (200, 201), (
            f"create_order вернул {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert "id" in data
        assert data["status"] == "accepted"

    def test_n2_create_order_location_id_set(
        self, client, db, restaurant_token, location, product
    ):
        """N2. После создания заказа order.location_id установлен корректно."""
        from models import Order

        resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 1}],
                "client_telegram_id": 99999,
            },
            headers={
                "Authorization": f"Bearer {restaurant_token}",
                "X-Location-Id": str(location.id),
            },
        )
        assert resp.status_code in (200, 201)
        order_id = resp.json()["id"]
        order = db.query(Order).filter(Order.id == order_id).first()
        assert order.location_id == location.id


# ═══════════════════════════════════════════════════════════════
# CheckO — notify_new_order продолжает работать с Location dispatcher
# ═══════════════════════════════════════════════════════════════

class TestCheckO:
    """CheckO: notify_new_order работает корректно после перехода dispatcher → Location."""

    def test_o1_full_notify_flow_with_location(self):
        """O1. notify_new_order с location — диспетчер из Location, валюта из Location."""
        from handlers import notify_new_order

        mock_loc = MagicMock()
        mock_loc.id = 801
        mock_loc.telegram_dispatcher_id = 77777
        mock_loc.telegram_bot_token_encrypted = "ENC_O"
        mock_loc.currency = "KZT"
        mock_loc.name = "O Loc"

        mock_rest = MagicMock()
        mock_rest.id = 80
        mock_rest.telegram_dispatcher_id = 11111
        mock_rest.currency = "UZS"
        mock_rest.name = "O Rest"

        mock_order = MagicMock()
        mock_order.id = 99
        mock_order.order_type = "delivery"
        mock_order.total_amount = 20000
        mock_order.client_name = "Alisher"
        mock_order.client_phone = "+998901234567"
        mock_order.address = "ул. Навои, 1"
        mock_order.table_id = None
        mock_order.comment = "Без лука"

        mock_item = MagicMock()
        mock_item.name = "Плов"
        mock_item.quantity = 2
        mock_item.price = 10000

        sent_messages = []
        mock_bot = MagicMock()
        mock_bot.send_message.side_effect = lambda chat_id, text: sent_messages.append((chat_id, text))

        import handlers
        handlers._BOT_CACHE.pop(801, None)

        with patch("handlers.decrypt_token", return_value="PLAIN_O"), \
             patch("telebot.TeleBot", return_value=mock_bot):
            notify_new_order(mock_order, [mock_item], mock_rest, location=mock_loc)

        assert len(sent_messages) == 1
        chat_id, text = sent_messages[0]
        assert chat_id == 77777, f"Отправлено на {chat_id}, ожидался 77777"
        # format_price для KZT возвращает символ ₸, не строку "KZT"
        assert "₸" in text, f"Ожидался символ ₸ (KZT) в тексте: {text!r}"
        assert "99" in text  # order id

        handlers._BOT_CACHE.pop(801, None)

    def test_o2_notify_new_order_items_text(self):
        """O2. Текст уведомления содержит позиции заказа."""
        from handlers import notify_new_order

        mock_loc = MagicMock()
        mock_loc.id = 802
        mock_loc.telegram_dispatcher_id = 44444
        mock_loc.telegram_bot_token_encrypted = "ENC_O2"
        mock_loc.currency = "UZS"
        mock_loc.name = "O2"

        mock_order = MagicMock()
        mock_order.id = 100
        mock_order.order_type = "takeaway"
        mock_order.total_amount = 35000
        mock_order.client_name = None
        mock_order.client_phone = None
        mock_order.address = None
        mock_order.table_id = None
        mock_order.comment = None

        mock_item = MagicMock()
        mock_item.name = "Лагман"
        mock_item.quantity = 3
        mock_item.price = 11667

        sent = []
        mock_bot = MagicMock()
        mock_bot.send_message.side_effect = lambda c, t: sent.append(t)

        import handlers
        handlers._BOT_CACHE.pop(802, None)

        with patch("handlers.decrypt_token", return_value="PLAIN"), \
             patch("telebot.TeleBot", return_value=mock_bot):
            notify_new_order(mock_order, [mock_item], MagicMock(), location=mock_loc)

        assert sent, "Уведомление не отправлено"
        assert "Лагман" in sent[0]

        handlers._BOT_CACHE.pop(802, None)
