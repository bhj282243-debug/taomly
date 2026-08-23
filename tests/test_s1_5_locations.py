"""
tests/test_s1_5_locations.py — S1-5: Location CRUD API + optional location_id filters

Covers:
  CheckA: LocationCreate  (POST /api/restaurants/me/locations)
  CheckB: LocationList    (GET  /api/restaurants/me/locations)
  CheckC: LocationDetail  (GET  /api/restaurants/me/locations/{id})
  CheckD: LocationUpdate  (PATCH /api/restaurants/me/locations/{id})
  CheckE: SoftDelete      (DELETE /api/restaurants/me/locations/{id})
  CheckF: OrderFilter     (GET /api/orders/restaurant/{id}?location_id=)
  CheckG: ReservationFilter (GET /api/reservations/restaurant/{id}?location_id=)
  CheckH: WaiterCallFilter  (GET /api/waiter-calls/restaurant/{id}?location_id=)

Invariants tested:
  I-1  Tenant isolation — Location принадлежит своему ресторану
  I-2  Brand isolation при ?location_id — чужая → 404
  I-4  Guard: нельзя деактивировать единственную активную Location
  I-5  Slug uniqueness conflict → 409
  I-6  ?location_id optional — без него backward compat
"""

import pytest
from sqlalchemy.exc import IntegrityError

from models import Location, Order, Reservation, WaiterCall


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _seed_order(db, restaurant, location, **kwargs) -> Order:
    o = Order(
        restaurant_id=restaurant.id,
        location_id=location.id,
        order_type=kwargs.get("order_type", "dine_in"),
        status=kwargs.get("status", "new"),
        total=kwargs.get("total", 15000),
        items_snapshot=[],
    )
    db.add(o)
    db.flush()
    return o


def _seed_reservation(db, restaurant, location, **kwargs) -> Reservation:
    from datetime import datetime, timedelta, timezone
    r = Reservation(
        restaurant_id=restaurant.id,
        location_id=location.id,
        client_name="Тест Тестов",
        client_phone="+998901234567",
        reservation_time=datetime.now(timezone.utc) + timedelta(days=1),
        guests_count=kwargs.get("guests_count", 2),
        status="pending",
    )
    db.add(r)
    db.flush()
    return r


def _seed_waiter_call(db, restaurant, location, table, **kwargs) -> WaiterCall:
    w = WaiterCall(
        restaurant_id=restaurant.id,
        location_id=location.id,
        table_id=table.id,
        status="pending",
    )
    db.add(w)
    db.flush()
    return w


# ══════════════════════════════════════════════════════════════════════════════
# CheckA: LocationCreate
# ══════════════════════════════════════════════════════════════════════════════

class TestLocationCreate:
    BASE_URL = "/api/restaurants/me/locations"

    def test_create_returns_201(self, client, restaurant):
        """A1. POST /me/locations → 201, поля совпадают."""
        payload = {
            "name": "Новый филиал",
            "slug": "chinar-branch-new",
            "timezone": "Asia/Tashkent",
            "delivery_fee": 5000,
            "min_order_amount": 30000,
            "currency": "UZS",
            "language": "uz",
            "is_waiter_call_enabled": False,
        }
        resp = client.post(self.BASE_URL, json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["name"] == "Новый филиал"
        assert data["slug"] == "chinar-branch-new"
        assert data["restaurant_id"] == restaurant.id
        assert data["is_active"] is True
        assert data["delivery_fee"] == 5000
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_slug_conflict_returns_409(self, client, restaurant, location):
        """A2 / I-5. Slug уже занят → 409."""
        # location fixture создаёт slug=restaurant.slug ("chinar")
        payload = {
            "name": "Дубль",
            "slug": location.slug,   # занят
            "timezone": "Asia/Tashkent",
            "delivery_fee": 0,
            "min_order_amount": 0,
            "currency": "UZS",
            "language": "uz",
        }
        resp = client.post(self.BASE_URL, json=payload)
        assert resp.status_code == 409, resp.text

    def test_restaurant_id_from_jwt_not_body(self, client, db, restaurant):
        """A3. restaurant_id берётся из JWT, не из тела запроса."""
        payload = {
            "name": "JWT Test",
            "slug": "jwt-test-location",
            "timezone": "Asia/Tashkent",
            "delivery_fee": 0,
            "min_order_amount": 0,
            "currency": "UZS",
            "language": "uz",
        }
        resp = client.post(self.BASE_URL, json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["restaurant_id"] == restaurant.id

    def test_invalid_currency_returns_422(self, client):
        """A4. Неизвестная currency → 422."""
        payload = {
            "name": "Test",
            "slug": "test-bad-currency",
            "timezone": "Asia/Tashkent",
            "delivery_fee": 0,
            "min_order_amount": 0,
            "currency": "JPY",   # не поддерживается
            "language": "uz",
        }
        resp = client.post(self.BASE_URL, json=payload)
        assert resp.status_code == 422, resp.text

    def test_invalid_slug_returns_422(self, client):
        """A5. Slug с заглавными буквами → 422."""
        payload = {
            "name": "Test",
            "slug": "BIG-LETTERS",
            "timezone": "Asia/Tashkent",
            "delivery_fee": 0,
            "min_order_amount": 0,
            "currency": "UZS",
            "language": "uz",
        }
        resp = client.post(self.BASE_URL, json=payload)
        assert resp.status_code == 422, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# CheckB: LocationList
# ══════════════════════════════════════════════════════════════════════════════

class TestLocationList:
    BASE_URL = "/api/restaurants/me/locations"

    def test_list_returns_own_locations_only(self, client, db, restaurant, location, restaurant2, location2):
        """B1 / I-1. Список содержит только Location своего ресторана."""
        resp = client.get(self.BASE_URL)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        ids = [loc["id"] for loc in data["locations"]]
        # Своя location есть
        assert location.id in ids
        # Чужая (restaurant2) нет
        assert location2.id not in ids

    def test_list_includes_inactive(self, client, db, restaurant, location):
        """B2. Список возвращает и неактивные Location."""
        # Создаём ещё одну и деактивируем через БД напрямую
        loc_inactive = Location(
            restaurant_id=restaurant.id,
            name="Inactive",
            slug="inactive-loc-test",
            is_active=False,
            timezone="Asia/Tashkent",
            delivery_fee=0,
            min_order_amount=0,
            currency="UZS",
            language="uz",
            is_waiter_call_enabled=False,
        )
        db.add(loc_inactive)
        db.flush()

        resp = client.get(self.BASE_URL)
        assert resp.status_code == 200, resp.text
        ids = [loc["id"] for loc in resp.json()["locations"]]
        assert loc_inactive.id in ids

    def test_list_total_matches(self, client, db, restaurant, location):
        """B3. total совпадает с длиной массива."""
        resp = client.get(self.BASE_URL)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == len(data["locations"])


# ══════════════════════════════════════════════════════════════════════════════
# CheckC: LocationDetail
# ══════════════════════════════════════════════════════════════════════════════

class TestLocationDetail:
    def test_get_own_location(self, client, location):
        """C1. GET /me/locations/{id} → 200, данные совпадают."""
        resp = client.get(f"/api/restaurants/me/locations/{location.id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == location.id
        assert data["slug"] == location.slug

    def test_get_foreign_location_returns_404(self, client, location2):
        """C2 / I-1. Чужая location → 404 (не раскрываем существование)."""
        resp = client.get(f"/api/restaurants/me/locations/{location2.id}")
        assert resp.status_code == 404, resp.text

    def test_get_nonexistent_returns_404(self, client):
        """C3. Несуществующий id → 404."""
        resp = client.get("/api/restaurants/me/locations/999999")
        assert resp.status_code == 404, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# CheckD: LocationUpdate
# ══════════════════════════════════════════════════════════════════════════════

class TestLocationUpdate:
    def test_patch_own_location(self, client, db, location):
        """D1. PATCH /me/locations/{id} → 200, поля обновлены."""
        resp = client.patch(
            f"/api/restaurants/me/locations/{location.id}",
            json={"name": "Обновлённое имя", "delivery_fee": 7000},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == "Обновлённое имя"
        assert data["delivery_fee"] == 7000
        # Остальные поля не изменились
        assert data["slug"] == location.slug

    def test_patch_partial_only_changes_sent_fields(self, client, location):
        """D2. PATCH передаёт только одно поле — остальные не меняются."""
        original_slug = location.slug
        resp = client.patch(
            f"/api/restaurants/me/locations/{location.id}",
            json={"delivery_fee": 1234},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["slug"] == original_slug

    def test_patch_foreign_location_returns_404(self, client, location2):
        """D3 / I-1. Чужая location → 404."""
        resp = client.patch(
            f"/api/restaurants/me/locations/{location2.id}",
            json={"name": "Хак"},
        )
        assert resp.status_code == 404, resp.text

    def test_patch_slug_conflict_returns_409(self, client, db, restaurant, location, location_a2):
        """D4 / I-5. PATCH slug на уже занятый → 409."""
        resp = client.patch(
            f"/api/restaurants/me/locations/{location_a2.id}",
            json={"slug": location.slug},   # занят
        )
        assert resp.status_code == 409, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# CheckE: SoftDelete
# ══════════════════════════════════════════════════════════════════════════════

class TestLocationSoftDelete:
    def test_delete_sets_is_active_false(self, client, db, restaurant, location, location_a2):
        """E1. DELETE → 204, is_active=False в БД."""
        # location_a2 — вторая активная Location того же ресторана
        # значит location можно деактивировать (не последняя)
        resp = client.delete(f"/api/restaurants/me/locations/{location.id}")
        assert resp.status_code == 204, resp.text
        db.expire(location)
        db.refresh(location)
        assert location.is_active is False

    def test_delete_last_active_returns_400(self, client, location):
        """E2 / I-4. Единственная активная Location → 400."""
        # В conftest у нас только одна active location для restaurant
        resp = client.delete(f"/api/restaurants/me/locations/{location.id}")
        assert resp.status_code == 400, resp.text
        assert "единственную" in resp.json()["detail"].lower()

    def test_delete_foreign_location_returns_404(self, client, location2):
        """E3 / I-1. Чужая location → 404."""
        resp = client.delete(f"/api/restaurants/me/locations/{location2.id}")
        assert resp.status_code == 404, resp.text

    def test_delete_already_inactive_is_idempotent(self, client, db, restaurant, location, location_a2):
        """E4. Повторный DELETE уже неактивной → 204 (идемпотентно)."""
        # Деактивируем через БД
        location.is_active = False
        db.flush()
        resp = client.delete(f"/api/restaurants/me/locations/{location.id}")
        assert resp.status_code == 204, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# CheckF: OrderFilter by location_id
# ══════════════════════════════════════════════════════════════════════════════

class TestOrderLocationFilter:
    def test_without_location_id_returns_all_orders(
        self, client, db, restaurant, location, location_a2
    ):
        """F1 / I-6. Без ?location_id → все заказы ресторана (backward compat)."""
        o1 = _seed_order(db, restaurant, location)
        o2 = _seed_order(db, restaurant, location_a2)

        resp = client.get(f"/api/orders/restaurant/{restaurant.id}")
        assert resp.status_code == 200, resp.text
        ids = [o["id"] for o in resp.json()]
        assert o1.id in ids
        assert o2.id in ids

    def test_with_location_id_filters_correctly(
        self, client, db, restaurant, location, location_a2
    ):
        """F2. С ?location_id → только заказы этой Location."""
        o1 = _seed_order(db, restaurant, location)
        o2 = _seed_order(db, restaurant, location_a2)

        resp = client.get(f"/api/orders/restaurant/{restaurant.id}?location_id={location.id}")
        assert resp.status_code == 200, resp.text
        ids = [o["id"] for o in resp.json()]
        assert o1.id in ids
        assert o2.id not in ids   # другая Location, не попадает

    def test_foreign_location_id_returns_404(
        self, client, restaurant, location2
    ):
        """F3 / I-2. ?location_id чужого ресторана → 404."""
        resp = client.get(
            f"/api/orders/restaurant/{restaurant.id}?location_id={location2.id}"
        )
        assert resp.status_code == 404, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# CheckG: ReservationFilter by location_id
# ══════════════════════════════════════════════════════════════════════════════

class TestReservationLocationFilter:
    def test_without_location_id_returns_all(
        self, client, db, restaurant, location, location_a2
    ):
        """G1 / I-6. Без ?location_id → все брони ресторана (backward compat)."""
        r1 = _seed_reservation(db, restaurant, location)
        r2 = _seed_reservation(db, restaurant, location_a2)

        resp = client.get(f"/api/reservations/restaurant/{restaurant.id}")
        assert resp.status_code == 200, resp.text
        ids = [r["id"] for r in resp.json()]
        assert r1.id in ids
        assert r2.id in ids

    def test_with_location_id_filters_correctly(
        self, client, db, restaurant, location, location_a2
    ):
        """G2. С ?location_id → только брони этой Location."""
        r1 = _seed_reservation(db, restaurant, location)
        r2 = _seed_reservation(db, restaurant, location_a2)

        resp = client.get(
            f"/api/reservations/restaurant/{restaurant.id}?location_id={location_a2.id}"
        )
        assert resp.status_code == 200, resp.text
        ids = [r["id"] for r in resp.json()]
        assert r2.id in ids
        assert r1.id not in ids

    def test_foreign_location_id_returns_404(
        self, client, restaurant, location2
    ):
        """G3 / I-2. ?location_id чужого ресторана → 404."""
        resp = client.get(
            f"/api/reservations/restaurant/{restaurant.id}?location_id={location2.id}"
        )
        assert resp.status_code == 404, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# CheckH: WaiterCallFilter by location_id
# ══════════════════════════════════════════════════════════════════════════════

class TestWaiterCallLocationFilter:
    def test_without_location_id_returns_all(
        self, client, db, restaurant, location, location_a2, table, table_a2
    ):
        """H1 / I-6. Без ?location_id → все вызовы ресторана (backward compat)."""
        w1 = _seed_waiter_call(db, restaurant, location, table=table)
        w2 = _seed_waiter_call(db, restaurant, location_a2, table=table_a2)

        resp = client.get(f"/api/waiter-calls/restaurant/{restaurant.id}")
        assert resp.status_code == 200, resp.text
        ids = [w["id"] for w in resp.json()]
        assert w1.id in ids
        assert w2.id in ids

    def test_with_location_id_filters_correctly(
        self, client, db, restaurant, location, location_a2, table, table_a2
    ):
        """H2. С ?location_id → только вызовы этой Location."""
        w1 = _seed_waiter_call(db, restaurant, location, table=table)
        w2 = _seed_waiter_call(db, restaurant, location_a2, table=table_a2)

        resp = client.get(
            f"/api/waiter-calls/restaurant/{restaurant.id}?location_id={location.id}"
        )
        assert resp.status_code == 200, resp.text
        ids = [w["id"] for w in resp.json()]
        assert w1.id in ids
        assert w2.id not in ids

    def test_foreign_location_id_returns_404(
        self, client, restaurant, location2
    ):
        """H3 / I-2. ?location_id чужого ресторана → 404."""
        resp = client.get(
            f"/api/waiter-calls/restaurant/{restaurant.id}?location_id={location2.id}"
        )
        assert resp.status_code == 404, resp.text
