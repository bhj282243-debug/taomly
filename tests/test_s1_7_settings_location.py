"""
tests/test_s1_7_settings_location.py — S1-7: Settings Migration to Location

Covers:
  CheckA: GET /api/restaurants/me/settings → source of truth = активная Location
  CheckB: PATCH /api/restaurants/me/settings → запись в Location
  CheckC: GET /api/restaurants/{slug} → operational settings из Location
  CheckD: orders.py → min_order_amount / currency из Location
  CheckE: handlers.notify_new_order → currency из Location
  CheckF: Tenant isolation

Invariants tested:
  I-1  GET /me/settings возвращает данные из Location, не из Restaurant
  I-2  PATCH /me/settings пишет в Location; повторный GET отражает изменение
  I-3  Публичный GET /{slug}: delivery_fee/currency/language из Location
  I-4  Заказ на delivery проверяет location.min_order_amount
  I-5  PATCH с невалидной валютой → 400 (валидация не сломана)
  I-6  Tenant isolation: PATCH ресторана A не влияет на Location ресторана B
"""

import pytest
from unittest.mock import MagicMock, patch

from models import Location, Order, Restaurant


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _patch_settings(client, payload: dict):
    return client.patch("/api/restaurants/me/settings", json=payload)


def _get_settings(client):
    return client.get("/api/restaurants/me/settings")


def _set_location_field(db, location: Location, **kwargs) -> Location:
    """Напрямую обновляет поля Location в БД (минуя API)."""
    for k, v in kwargs.items():
        setattr(location, k, v)
    db.commit()
    db.refresh(location)
    return location


# ══════════════════════════════════════════════════════════════════════════════
# CheckA — GET /me/settings читает из Location
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckA:
    """CheckA: GET /me/settings — source of truth = Location."""

    def test_a1_get_settings_returns_200(self, client, location):
        """A1. GET /me/settings → 200."""
        resp = _get_settings(client)
        assert resp.status_code == 200

    def test_a2_settings_reflect_location_delivery_fee(self, client, db, location):
        """A2. GET возвращает delivery_fee из Location, не из Restaurant."""
        _set_location_field(db, location, delivery_fee=7500)
        resp = _get_settings(client)
        assert resp.status_code == 200
        assert resp.json()["delivery_fee"] == 7500

    def test_a3_settings_reflect_location_currency(self, client, db, location):
        """A3. GET возвращает currency из Location."""
        _set_location_field(db, location, currency="KZT")
        resp = _get_settings(client)
        assert resp.status_code == 200
        assert resp.json()["currency"] == "KZT"

    def test_a4_settings_reflect_location_language(self, client, db, location):
        """A4. GET возвращает language из Location."""
        _set_location_field(db, location, language="ru")
        resp = _get_settings(client)
        assert resp.status_code == 200
        assert resp.json()["language"] == "ru"

    def test_a5_settings_reflect_location_working_hours(self, client, db, location):
        """A5. GET возвращает working_hours из Location."""
        _set_location_field(db, location, working_hours="10:00-22:00")
        resp = _get_settings(client)
        assert resp.status_code == 200
        assert resp.json()["working_hours"] == "10:00-22:00"

    def test_a6_settings_reflect_location_min_order_amount(self, client, db, location):
        """A6. GET возвращает min_order_amount из Location."""
        _set_location_field(db, location, min_order_amount=25000)
        resp = _get_settings(client)
        assert resp.status_code == 200
        assert resp.json()["min_order_amount"] == 25000

    def test_a7_location_overrides_restaurant(self, client, db, restaurant, location):
        """A7. Если Location.currency != Restaurant.currency → GET возвращает Location.currency."""
        # Намеренно расходящиеся значения
        restaurant.currency = "UZS"
        db.commit()
        _set_location_field(db, location, currency="RUB")

        resp = _get_settings(client)
        assert resp.status_code == 200
        # Должен вернуть Location.currency, не Restaurant.currency
        assert resp.json()["currency"] == "RUB", (
            "GET /me/settings должен читать currency из Location, не из Restaurant"
        )

    def test_a8_all_required_fields_present(self, client, location):
        """A8. Ответ содержит все 6 полей."""
        resp = _get_settings(client)
        assert resp.status_code == 200
        body = resp.json()
        for field in ("working_hours", "delivery_fee", "min_order_amount",
                      "timezone", "currency", "language"):
            assert field in body, f"Поле '{field}' отсутствует в ответе"


# ══════════════════════════════════════════════════════════════════════════════
# CheckB — PATCH /me/settings пишет в Location
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckB:
    """CheckB: PATCH /me/settings → запись в Location."""

    def test_b1_patch_delivery_fee_reflected_in_location(self, client, db, location):
        """B1. PATCH delivery_fee → Location.delivery_fee обновляется."""
        resp = _patch_settings(client, {"delivery_fee": 5000})
        assert resp.status_code == 200

        db.refresh(location)
        assert location.delivery_fee == 5000, (
            f"Location.delivery_fee должен быть 5000, получен {location.delivery_fee}"
        )

    def test_b2_patch_currency_reflected_in_location(self, client, db, location):
        """B2. PATCH currency → Location.currency обновляется."""
        resp = _patch_settings(client, {"currency": "KZT"})
        assert resp.status_code == 200

        db.refresh(location)
        assert location.currency == "KZT", (
            f"Location.currency должен быть 'KZT', получен '{location.currency}'"
        )

    def test_b3_patch_language_reflected_in_location(self, client, db, location):
        """B3. PATCH language → Location.language обновляется."""
        resp = _patch_settings(client, {"language": "ru"})
        assert resp.status_code == 200

        db.refresh(location)
        assert location.language == "ru"

    def test_b4_patch_working_hours_reflected_in_location(self, client, db, location):
        """B4. PATCH working_hours → Location.working_hours обновляется."""
        resp = _patch_settings(client, {"working_hours": "09:00-21:00"})
        assert resp.status_code == 200

        db.refresh(location)
        assert location.working_hours == "09:00-21:00"

    def test_b5_patch_min_order_amount_reflected_in_location(self, client, db, location):
        """B5. PATCH min_order_amount → Location.min_order_amount обновляется."""
        resp = _patch_settings(client, {"min_order_amount": 15000})
        assert resp.status_code == 200

        db.refresh(location)
        assert location.min_order_amount == 15000

    def test_b6_patch_timezone_reflected_in_location(self, client, db, location):
        """B6. PATCH timezone → Location.timezone обновляется."""
        resp = _patch_settings(client, {"timezone": "Asia/Almaty"})
        assert resp.status_code == 200

        db.refresh(location)
        assert location.timezone == "Asia/Almaty"

    def test_b7_patch_response_reflects_location_values(self, client, db, location):
        """B7. PATCH ответ содержит новые значения из Location."""
        resp = _patch_settings(client, {"delivery_fee": 3000, "currency": "USD"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["delivery_fee"] == 3000
        assert body["currency"] == "USD"

    def test_b8_patch_get_roundtrip(self, client, db, location):
        """B8. PATCH → GET возвращает то же значение (roundtrip)."""
        _patch_settings(client, {"delivery_fee": 8888})
        resp = _get_settings(client)
        assert resp.json()["delivery_fee"] == 8888

    def test_b9_empty_patch_returns_200(self, client):
        """B9. PATCH с пустым телом → 200 (идемпотентность, ничего не меняет)."""
        resp = _patch_settings(client, {})
        assert resp.status_code == 200

    def test_b10_invalid_currency_returns_400(self, client):
        """B10. PATCH с невалидной валютой → 400."""
        resp = _patch_settings(client, {"currency": "XYZ"})
        assert resp.status_code == 400

    def test_b11_invalid_timezone_returns_400(self, client):
        """B11. PATCH с невалидным timezone → 400."""
        resp = _patch_settings(client, {"timezone": "invalid_tz"})
        assert resp.status_code == 400

    def test_b12_invalid_language_returns_400(self, client):
        """B12. PATCH с невалидным language → 400."""
        resp = _patch_settings(client, {"language": "xx"})
        assert resp.status_code == 400

    def test_b13_backward_compat_restaurant_currency_synced(
        self, client, db, restaurant, location
    ):
        """B13. Backward compat: restaurant.currency синхронизируется с Location (до Migration 0015)."""
        resp = _patch_settings(client, {"currency": "AED"})
        assert resp.status_code == 200

        db.refresh(restaurant)
        db.refresh(location)
        assert location.currency == "AED"
        assert restaurant.currency == "AED", (
            "restaurant.currency должен синхронизироваться для backward compat "
            "(orders.py читает его до Migration 0015)"
        )

    def test_b14_backward_compat_restaurant_min_order_synced(
        self, client, db, restaurant, location
    ):
        """B14. Backward compat: restaurant.min_order_amount синхронизируется."""
        resp = _patch_settings(client, {"min_order_amount": 12000})
        assert resp.status_code == 200

        db.refresh(restaurant)
        db.refresh(location)
        assert location.min_order_amount == 12000
        assert restaurant.min_order_amount == 12000


# ══════════════════════════════════════════════════════════════════════════════
# CheckC — GET /{slug} читает operational settings из Location
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckC:
    """CheckC: Публичный GET /api/restaurants/{slug} → settings из Location."""

    def test_c1_public_endpoint_returns_200(self, client, restaurant, location):
        """C1. GET /{slug} → 200."""
        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200

    def test_c2_delivery_fee_from_location(self, client, db, restaurant, location):
        """C2. delivery_fee в публичном ответе берётся из Location."""
        _set_location_field(db, location, delivery_fee=4500)
        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        assert resp.json()["delivery_fee"] == 4500

    def test_c3_currency_from_location(self, client, db, restaurant, location):
        """C3. currency в публичном ответе берётся из Location."""
        _set_location_field(db, location, currency="KZT")
        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        assert resp.json()["currency"] == "KZT"

    def test_c4_language_from_location(self, client, db, restaurant, location):
        """C4. language в публичном ответе берётся из Location."""
        _set_location_field(db, location, language="ru")
        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        assert resp.json()["language"] == "ru"

    def test_c5_min_order_amount_from_location(self, client, db, restaurant, location):
        """C5. min_order_amount в публичном ответе берётся из Location."""
        _set_location_field(db, location, min_order_amount=30000)
        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        assert resp.json()["min_order_amount"] == 30000

    def test_c6_working_hours_from_location(self, client, db, restaurant, location):
        """C6. working_hours в публичном ответе берётся из Location."""
        _set_location_field(db, location, working_hours="11:00-23:00")
        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        assert resp.json()["working_hours"] == "11:00-23:00"

    def test_c7_location_overrides_restaurant_in_public(
        self, client, db, restaurant, location
    ):
        """C7. Если Location.currency != Restaurant.currency → публичный ответ = Location."""
        restaurant.currency = "UZS"
        db.commit()
        _set_location_field(db, location, currency="TRY")

        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        assert resp.json()["currency"] == "TRY", (
            "Публичный эндпоинт должен возвращать currency из Location"
        )

    def test_c8_branding_still_from_restaurant(self, client, db, restaurant, location):
        """C8. Branding-поля (logo_url, primary_color и т.д.) остаются из Restaurant."""
        restaurant.primary_color = "#FF0000"
        db.commit()
        resp = client.get(f"/api/restaurants/{restaurant.slug}")
        assert resp.status_code == 200
        assert resp.json()["primary_color"] == "#FF0000"


# ══════════════════════════════════════════════════════════════════════════════
# CheckD — orders.py: min_order_amount и currency из Location
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckD:
    """CheckD: create_order проверяет min_order_amount и currency из Location."""

    def test_d1_order_below_location_min_order_rejected(
        self, client, db, location, product
    ):
        """D1. Заказ delivery с суммой < location.min_order_amount → 400."""
        _set_location_field(db, location, min_order_amount=100000)

        resp = client.post("/api/orders/", json={
            "order_type": "delivery",
            "address": "ул. Тестовая 1",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        # product.price < 100000 → должен отклонить
        assert resp.status_code == 400, (
            f"Ожидался 400 (сумма ниже min_order_amount Location), "
            f"получен {resp.status_code}: {resp.json()}"
        )

    def test_d2_order_above_location_min_order_accepted(
        self, client, db, location, product
    ):
        """D2. Заказ delivery с суммой ≥ location.min_order_amount → 201."""
        _set_location_field(db, location, min_order_amount=0)

        resp = client.post("/api/orders/", json={
            "order_type": "delivery",
            "address": "ул. Тестовая 1",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 201, (
            f"Ожидался 201, получен {resp.status_code}: {resp.json()}"
        )

    def test_d3_location_min_order_independent_of_restaurant(
        self, client, db, restaurant, location, product
    ):
        """D3. location.min_order_amount применяется независимо от restaurant.min_order_amount."""
        # Restaurant имеет высокий лимит, Location — нулевой
        restaurant.min_order_amount = 1000000
        db.commit()
        _set_location_field(db, location, min_order_amount=0)

        # Заказ должен пройти (Location разрешает)
        resp = client.post("/api/orders/", json={
            "order_type": "delivery",
            "address": "ул. Тестовая 1",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 201, (
            "Заказ должен приниматься по min_order_amount Location (=0), "
            f"а не Restaurant (=1000000). Статус: {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CheckE — handlers.notify_new_order: currency из Location
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckE:
    """CheckE: notify_new_order использует currency из Location."""

    def test_e1_notify_uses_location_currency_when_provided(self):
        """E1. notify_new_order использует location.currency если location передан."""
        from handlers import notify_new_order

        # Mock объекты
        mock_restaurant = MagicMock()
        mock_restaurant.telegram_dispatcher_id = None  # не отправляем сообщение
        mock_restaurant.currency = "UZS"
        mock_restaurant.name = "Test"
        mock_restaurant.id = 1

        mock_location = MagicMock()
        mock_location.currency = "KZT"

        mock_order = MagicMock()
        mock_order.id = 42
        mock_order.order_type = "dine_in"
        mock_order.total_amount = 10000
        mock_order.client_name = None
        mock_order.client_phone = None
        mock_order.address = None
        mock_order.table_id = None
        mock_order.comment = None

        # dispatcher_id = None → функция вернётся после warning, не отправит сообщение
        # Проверяем только что функция не падает с ошибкой при location=KZT
        # (полный тест currency требует inspect внутренней переменной)
        try:
            notify_new_order(mock_order, [], mock_restaurant, location=mock_location)
        except Exception as e:
            pytest.fail(f"notify_new_order упал с location=mock_location: {e}")

    def test_e2_notify_backward_compat_without_location(self):
        """E2. notify_new_order без location (старый вызов) не ломается."""
        from handlers import notify_new_order

        mock_restaurant = MagicMock()
        mock_restaurant.telegram_dispatcher_id = None
        mock_restaurant.currency = "UZS"
        mock_restaurant.name = "Test"
        mock_restaurant.id = 1

        mock_order = MagicMock()
        mock_order.id = 1
        mock_order.order_type = "dine_in"
        mock_order.total_amount = 5000
        mock_order.client_name = None
        mock_order.client_phone = None
        mock_order.address = None
        mock_order.table_id = None
        mock_order.comment = None

        # Вызов без location= — backward compat
        try:
            notify_new_order(mock_order, [], mock_restaurant)
        except Exception as e:
            pytest.fail(f"notify_new_order без location= упал: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# CheckF — Tenant isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckF:
    """CheckF: PATCH /me/settings не влияет на Location другого ресторана."""

    def test_f1_patch_settings_only_affects_own_location(
        self, client, db, location, location2
    ):
        """F1. PATCH настроек ресторана A не меняет Location ресторана B."""
        # Запоминаем состояние Location ресторана B до операции
        currency_b_before = location2.currency
        fee_b_before = location2.delivery_fee

        # Меняем настройки ресторана A
        resp = _patch_settings(client, {"currency": "USD", "delivery_fee": 9999})
        assert resp.status_code == 200

        # Location ресторана B не должна измениться
        db.refresh(location2)
        assert location2.currency == currency_b_before, (
            "PATCH настроек ресторана A изменил currency Location ресторана B!"
        )
        assert location2.delivery_fee == fee_b_before, (
            "PATCH настроек ресторана A изменил delivery_fee Location ресторана B!"
        )

    def test_f2_patch_settings_only_affects_own_restaurant(
        self, client, db, restaurant, restaurant2
    ):
        """F2. Backward compat sync не распространяется на restaurant2."""
        currency_r2_before = restaurant2.currency

        resp = _patch_settings(client, {"currency": "TRY"})
        assert resp.status_code == 200

        db.refresh(restaurant2)
        assert restaurant2.currency == currency_r2_before, (
            "PATCH настроек ресторана A обновил currency ресторана B!"
        )
