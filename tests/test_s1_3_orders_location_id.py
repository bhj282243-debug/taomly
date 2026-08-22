"""
tests/test_s1_3_orders_location_id.py — S1-3: orders.location_id

Проверки по спецификации:

  A. Backfill: orders.location_id IS NOT NULL = 0
  B. Cross-brand consistency: order.restaurant_id == location.restaurant_id
  C. Location ownership: Order created in Location A has location_id = A
  D. Cross-location table: Table Location A + request Location B → reject (400)
  E. Cross-brand table: Table Brand A + request Brand B → reject (404 location / 404 product)
  F. Delivery: Delivery order without table still gets location_id
  G. Legacy compat: restaurant_id remains populated during transition
  H. Different Locations same Brand: Orders created independently in A1 and A2
  I. Location deletion: Location with Orders cannot be deleted (FK RESTRICT)

  Дополнительно (из расширенных требований):
  J. OrderResponse содержит location_id
  K. X-Location-Id чужого Brand → 404 (cross-brand injection protection)
  L. X-Location-Id inactive Location → 404
  M. Brand quota суммирует Orders всех Locations (Brand-level quota)
  N. Создание нового Order: restaurant_id == location.restaurant_id (consistency)
"""

import pytest
from sqlalchemy.exc import IntegrityError

from models import Location, Order, OrderItem, RestaurantTable


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _seed_order(db, restaurant, location, order_type="takeaway", **kwargs) -> Order:
    """Создаёт Order напрямую в БД (минует HTTP стек)."""
    o = Order(
        restaurant_id=restaurant.id,
        location_id=location.id,
        client_telegram_id=111222333,
        client_name="Test",
        order_type=order_type,
        total_amount=10000,
        status="accepted",
        **kwargs,
    )
    db.add(o)
    db.flush()
    return o


# ══════════════════════════════════════════════════════════════════════════════
# A. BACKFILL: все строки имеют location_id
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckA:
    """
    A. Backfill verification.

    После migration 0012 ни один Order не должен иметь location_id IS NULL.
    Тест создаёт несколько Orders через DB и проверяет, что все имеют location_id.
    """

    def test_no_null_location_id_after_seed(self, db, restaurant, location):
        """A1. Новые Orders имеют location_id (не NULL)."""
        _seed_order(db, restaurant, location)
        _seed_order(db, restaurant, location)

        null_count = (
            db.query(Order)
            .filter(Order.location_id.is_(None))
            .count()
        )
        assert null_count == 0, (
            f"A FAIL: {null_count} orders have location_id IS NULL"
        )

    def test_location_id_not_nullable_in_model(self):
        """A2. Колонка location_id в модели определена как NOT NULL."""
        col = Order.__table__.c["location_id"]
        assert not col.nullable, (
            "A2 FAIL: Order.location_id should be NOT NULL in model"
        )

    def test_location_id_has_fk(self):
        """A3. location_id имеет FK на locations.id."""
        col = Order.__table__.c["location_id"]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "locations.id" in fk_targets, (
            f"A3 FAIL: location_id FK not pointing to locations.id. Got: {fk_targets}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# B. CROSS-BRAND CONSISTENCY
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckB:
    """
    B. Cross-brand consistency.

    order.restaurant_id MUST match location.restaurant_id.
    Нарушение означает, что Order создан с location другого Brand — это corruption.
    """

    def test_cross_brand_consistency(self, db, restaurant, location):
        """B1. Все Orders имеют matching restaurant_id / location.restaurant_id."""
        _seed_order(db, restaurant, location)

        bad = (
            db.query(Order)
            .join(Location, Location.id == Order.location_id)
            .filter(Order.restaurant_id != Location.restaurant_id)
            .all()
        )
        assert len(bad) == 0, (
            f"B FAIL: {len(bad)} orders have cross-brand inconsistency"
        )

    def test_seed_order_restaurant_matches_location(self, db, restaurant, location):
        """B2. _seed_order гарантирует consistency: restaurant_id == location.restaurant_id."""
        o = _seed_order(db, restaurant, location)
        db.refresh(o)
        assert o.restaurant_id == location.restaurant_id, (
            f"B2 FAIL: order.restaurant_id={o.restaurant_id} "
            f"!= location.restaurant_id={location.restaurant_id}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# C. LOCATION OWNERSHIP
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckC:
    """
    C. Location ownership.

    Order создан в Location A → order.location_id == A.id.
    Проверяется через HTTP (create_order) с X-Location-Id.
    """

    def test_order_gets_correct_location_id_via_http(
        self, client, db, product, location
    ):
        """
        C1. POST /api/orders/ с X-Location-Id → order.location_id == location.id.
        client fixture передаёт X-Location-Id = location.id в default headers.
        """
        resp = client.post("/api/orders/", json={
            "order_type": "takeaway",
            "items": [{"product_id": product.id, "quantity": 1}],
            "client_name": "Тест",
        })
        assert resp.status_code == 201, (
            f"C1: Expected 201, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["location_id"] == location.id, (
            f"C FAIL: order.location_id={data['location_id']} != location.id={location.id}"
        )

    def test_order_location_id_in_db(
        self, client, db, product, location
    ):
        """C2. location_id корректно записан в БД (не только в ответе)."""
        resp = client.post("/api/orders/", json={
            "order_type": "takeaway",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 201
        order_id = resp.json()["id"]

        order = db.query(Order).filter(Order.id == order_id).first()
        assert order is not None
        assert order.location_id == location.id, (
            f"C2 FAIL: DB location_id={order.location_id} != location.id={location.id}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# D. CROSS-LOCATION TABLE REJECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckD:
    """
    D. Cross-location table rejection.

    Тест: клиент с X-Location-Id=A1 пытается создать dine_in заказ
    с table.location_id=A2 (другая Location того же Brand) → 400.

    Это принципиально новый check S1-3:
    - Стол и Location должны совпадать.
    - Brand одинаковый → не 404, а 400 (стол найден, но не в этой Location).
    """

    def test_dine_in_with_table_from_other_location_same_brand_rejected(
        self, client, db, product, location, location_a2, table_a2
    ):
        """
        D. Table в Location A2, запрос с X-Location-Id=A1 → 400.

        table_a2 fixture: restaurant=restaurant, location=location_a2.
        client fixture default header: X-Location-Id = location.id (A1).
        """
        resp = client.post("/api/orders/", json={
            "order_type": "dine_in",
            "table_id": table_a2.id,      # стол в A2
            "items": [{"product_id": product.id, "quantity": 1}],
            "client_name": "Тест",
        })
        # X-Location-Id=A1, table.location_id=A2 → 400
        assert resp.status_code == 400, (
            f"D FAIL: Expected 400 (cross-location table), "
            f"got {resp.status_code}: {resp.text}"
        )
        assert "локации" in resp.json()["detail"].lower() or "location" in resp.json()["detail"].lower(), (
            f"D FAIL: Wrong error message: {resp.json()['detail']}"
        )

    def test_dine_in_with_table_from_correct_location_passes(
        self, client, db, product, location, table
    ):
        """
        D-pass. Table в Location A1, запрос с X-Location-Id=A1 → 201.
        table fixture: restaurant=restaurant, location=location.
        """
        resp = client.post("/api/orders/", json={
            "order_type": "dine_in",
            "table_id": table.id,          # стол в A1
            "items": [{"product_id": product.id, "quantity": 1}],
            "client_name": "Тест",
        })
        assert resp.status_code == 201, (
            f"D-pass FAIL: Expected 201 (correct location table), "
            f"got {resp.status_code}: {resp.text}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# E. CROSS-BRAND TABLE REJECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckE:
    """
    E. Cross-brand table rejection.

    Тест 1: X-Location-Id чужого Brand → 404 на этапе Location резолва.
    Тест 2: Location правильная, но table.restaurant_id != restaurant.id → 404.

    Оба пути защищают от cross-brand injection.
    """

    def test_x_location_id_from_other_brand_rejected(
        self, client, db, product, location_b1
    ):
        """
        E1. X-Location-Id = location Brand B, клиент Brand A → 404.

        Location B не принадлежит restaurant (Brand A) →
        запрос отклоняется ещё до дохождения до table/product check.
        """
        resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 1}],
                "client_name": "Атакующий",
            },
            headers={"X-Location-Id": str(location_b1.id)},  # чужой Brand
        )
        # Location не принадлежит restaurant → 404
        assert resp.status_code == 404, (
            f"E1 FAIL: Expected 404 (cross-brand location), "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_table_from_other_brand_rejected_404(
        self, client, db, product, location, restaurant2, location_b1
    ):
        """
        E2. Стол Brand B через корректный X-Location-Id Brand A → 404.

        table.restaurant_id != restaurant.id → 404 (stол не найден в этом ресторане).
        """
        # Создаём стол в Brand B (location_b1)
        table_b = RestaurantTable(
            restaurant_id=restaurant2.id,
            location_id=location_b1.id,
            table_number="B-99",
        )
        db.add(table_b)
        db.flush()

        resp = client.post("/api/orders/", json={
            "order_type": "dine_in",
            "table_id": table_b.id,        # стол Brand B
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        # table.restaurant_id != restaurant.id → 404
        assert resp.status_code == 404, (
            f"E2 FAIL: Expected 404 (cross-brand table), "
            f"got {resp.status_code}: {resp.text}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# F. DELIVERY ORDER WITHOUT TABLE GETS LOCATION_ID
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckF:
    """
    F. Delivery order без table_id всё равно получает location_id.

    Delivery не привязан к столу, но location_id обязателен для всех Orders.
    """

    def test_delivery_order_gets_location_id(
        self, client, db, product, location
    ):
        """F. POST delivery order → location_id присутствует и корректен."""
        resp = client.post("/api/orders/", json={
            "order_type": "delivery",
            "address": "ул. Навои, 1",
            "items": [{"product_id": product.id, "quantity": 1}],
            "client_name": "Курьер",
        })
        assert resp.status_code == 201, (
            f"F FAIL: Expected 201, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert "location_id" in data, "F FAIL: location_id absent from OrderResponse"
        assert data["location_id"] == location.id, (
            f"F FAIL: location_id={data['location_id']} != location.id={location.id}"
        )
        assert data.get("table_id") is None, "F FAIL: delivery should have no table_id"

    def test_takeaway_order_gets_location_id(
        self, client, db, product, location
    ):
        """F-takeaway. Takeaway Order без table тоже получает location_id."""
        resp = client.post("/api/orders/", json={
            "order_type": "takeaway",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["location_id"] == location.id


# ══════════════════════════════════════════════════════════════════════════════
# G. LEGACY COMPATIBILITY: restaurant_id остаётся
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckG:
    """
    G. Legacy compatibility.

    orders.restaurant_id НЕ удалён — он остаётся до Migration 0015.
    При создании нового Order оба поля заполнены.
    """

    def test_restaurant_id_column_exists_in_model(self):
        """G1. Колонка restaurant_id присутствует в модели."""
        assert "restaurant_id" in Order.__table__.c, (
            "G FAIL: restaurant_id column missing from Order model — must NOT be dropped until 0015"
        )

    def test_legacy_restaurant_id_populated_on_create(
        self, client, db, product, restaurant, location
    ):
        """G2. При создании Order через HTTP оба поля заполнены."""
        resp = client.post("/api/orders/", json={
            "order_type": "takeaway",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 201
        order_id = resp.json()["id"]

        order = db.query(Order).filter(Order.id == order_id).first()
        assert order.restaurant_id is not None, "G2 FAIL: restaurant_id is NULL"
        assert order.restaurant_id == restaurant.id, (
            f"G2 FAIL: restaurant_id={order.restaurant_id} != restaurant.id={restaurant.id}"
        )
        assert order.location_id == location.id, (
            f"G2 FAIL: location_id={order.location_id} != location.id={location.id}"
        )

    def test_restaurant_id_consistency_with_location(
        self, client, db, product, restaurant, location
    ):
        """G3. restaurant_id == location.restaurant_id (consistency invariant)."""
        resp = client.post("/api/orders/", json={
            "order_type": "takeaway",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 201
        order_id = resp.json()["id"]

        order = db.query(Order).filter(Order.id == order_id).first()
        loc = db.query(Location).filter(Location.id == order.location_id).first()
        assert order.restaurant_id == loc.restaurant_id, (
            f"G3 FAIL: restaurant_id={order.restaurant_id} "
            f"!= location.restaurant_id={loc.restaurant_id}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# H. DIFFERENT LOCATIONS SAME BRAND — независимые Orders
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckH:
    """
    H. Different Locations, same Brand.

    Orders могут создаваться независимо в A1 и A2.
    Оба привязаны к правильной Location.
    Tenant-isolation: ни один из них не видит Orders другой Location (будущий S1-8).
    """

    def test_orders_in_two_locations_same_brand(
        self, db, restaurant, location, location_a2
    ):
        """H1. Можно создать Orders в A1 и A2 независимо."""
        o1 = _seed_order(db, restaurant, location)
        o2 = _seed_order(db, restaurant, location_a2)

        db.refresh(o1)
        db.refresh(o2)

        assert o1.location_id == location.id, "H FAIL: o1 wrong location"
        assert o2.location_id == location_a2.id, "H FAIL: o2 wrong location"
        assert o1.location_id != o2.location_id, "H FAIL: both orders in same location"
        # Оба принадлежат одному Brand
        assert o1.restaurant_id == restaurant.id
        assert o2.restaurant_id == restaurant.id

    def test_http_create_in_second_location_of_same_brand(
        self, client, db, product, restaurant, location, location_a2
    ):
        """H2. HTTP: X-Location-Id=A2, тот же Brand → 201."""
        resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 1}],
            },
            headers={"X-Location-Id": str(location_a2.id)},  # второй Location
        )
        assert resp.status_code == 201, (
            f"H2 FAIL: Expected 201 for second location of same brand, "
            f"got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["location_id"] == location_a2.id, (
            f"H2 FAIL: location_id={data['location_id']} != location_a2.id={location_a2.id}"
        )
        assert data["restaurant_id"] == restaurant.id, (
            f"H2 FAIL: restaurant_id wrong for second location"
        )


# ══════════════════════════════════════════════════════════════════════════════
# I. LOCATION DELETION FORBIDDEN (FK RESTRICT)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckI:
    """
    I. Location с Orders нельзя физически удалить.

    FK: orders.location_id REFERENCES locations(id) ON DELETE RESTRICT.
    Попытка DELETE locations WHERE id = ? при наличии Orders → IntegrityError.
    """

    def test_cannot_delete_location_with_orders(self, db, restaurant, location):
        """I. DELETE location с Orders → IntegrityError (FK RESTRICT)."""
        _seed_order(db, restaurant, location)

        with pytest.raises((IntegrityError, Exception)) as exc_info:
            db.query(Location).filter(Location.id == location.id).delete()
            db.flush()

        db.rollback()

        # Проверяем что ошибка связана с FK (constraint violation)
        err_str = str(exc_info.value).lower()
        assert any(kw in err_str for kw in ["foreign", "constraint", "violat", "restrict"]), (
            f"I FAIL: Expected FK constraint error, got: {exc_info.value}"
        )

    def test_fk_restrict_in_model_metadata(self):
        """I2. FK ondelete=RESTRICT присутствует в metadata модели."""
        col = Order.__table__.c["location_id"]
        for fk in col.foreign_keys:
            if "locations" in fk.target_fullname:
                assert fk.ondelete == "RESTRICT", (
                    f"I2 FAIL: Expected ON DELETE RESTRICT, got: {fk.ondelete}"
                )
                return
        pytest.fail("I2 FAIL: FK on location_id not found in model metadata")


# ══════════════════════════════════════════════════════════════════════════════
# J. ORDER RESPONSE СОДЕРЖИТ LOCATION_ID
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckJ:
    """
    J. OrderResponse включает location_id.

    POST /api/orders/ возвращает location_id в теле ответа.
    GET /api/orders/restaurant/{id} → каждый item имеет location_id.
    """

    def test_order_response_contains_location_id(
        self, client, product, location
    ):
        """J1. POST → ответ содержит location_id."""
        resp = client.post("/api/orders/", json={
            "order_type": "takeaway",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "location_id" in data, (
            f"J FAIL: 'location_id' absent from OrderResponse. Keys: {list(data.keys())}"
        )
        assert data["location_id"] == location.id

    def test_order_list_response_contains_location_id(
        self, client, db, product, restaurant, location
    ):
        """J2. GET /api/orders/restaurant/{id} → каждый Order имеет location_id."""
        # Создаём заказ
        _seed_order(db, restaurant, location)

        resp = client.get(f"/api/orders/restaurant/{restaurant.id}")
        assert resp.status_code == 200

        orders = resp.json()
        assert len(orders) > 0, "J2 FAIL: No orders returned"
        for o in orders:
            assert "location_id" in o, (
                f"J2 FAIL: 'location_id' absent from order in list. Keys: {list(o.keys())}"
            )
            assert o["location_id"] == location.id


# ══════════════════════════════════════════════════════════════════════════════
# K. X-LOCATION-ID ЧУЖОГО BRAND → 404
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckK:
    """
    K. Cross-brand location injection protection.

    Клиент Brand A передаёт X-Location-Id = Location Brand B.
    Ожидаем 404 — Location не принадлежит restaurant (Brand A).
    """

    def test_cross_brand_location_header_rejected(
        self, client, product, location_b1
    ):
        """K. X-Location-Id от другого Brand → 404."""
        resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 1}],
            },
            headers={"X-Location-Id": str(location_b1.id)},
        )
        assert resp.status_code == 404, (
            f"K FAIL: Expected 404 (cross-brand location injection), "
            f"got {resp.status_code}: {resp.text}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# L. INACTIVE LOCATION → 404
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckL:
    """
    L. Деактивированная Location не принимает заказы.

    is_active=False → create_order возвращает 404.
    """

    def test_inactive_location_rejected(
        self, client, db, product, restaurant, location
    ):
        """L. is_active=False Location → 404."""
        # Деактивируем location
        location.is_active = False
        db.flush()

        resp = client.post("/api/orders/", json={
            "order_type": "takeaway",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 404, (
            f"L FAIL: Expected 404 (inactive location), "
            f"got {resp.status_code}: {resp.text}"
        )

        # Восстанавливаем для других тестов
        location.is_active = True
        db.flush()


# ══════════════════════════════════════════════════════════════════════════════
# M. BRAND QUOTA — суммирует Orders всех Locations
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckM:
    """
    M. Brand-level quota суммирует Orders из всех Locations.

    Quota остаётся на уровне restaurant_id (Brand) — S1-8.
    Orders из Location A1 + Location A2 суммируются для quota Brand A.
    """

    def test_quota_counts_orders_across_locations(
        self, db, restaurant, location, location_a2
    ):
        """M. Orders в A1 и A2 вместе учитываются в Brand quota."""
        # 3 заказа в A1, 2 заказа в A2
        for _ in range(3):
            _seed_order(db, restaurant, location)
        for _ in range(2):
            _seed_order(db, restaurant, location_a2)

        from datetime import datetime, timezone
        from calendar import monthrange

        now = datetime.now(timezone.utc)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

        from sqlalchemy import text
        result = db.execute(
            text("""
                SELECT COUNT(*) AS cnt
                FROM orders
                WHERE restaurant_id = :rid
                  AND created_at >= :start
            """),
            {"rid": restaurant.id, "start": month_start},
        ).scalar()

        assert result == 5, (
            f"M FAIL: Expected 5 orders (3 A1 + 2 A2), quota query returned {result}"
        )

    def test_quota_does_not_count_other_brand_orders(
        self, db, restaurant, location, restaurant2, location2
    ):
        """M2. Quota Brand A не включает Orders Brand B."""
        from datetime import datetime, timezone
        from sqlalchemy import text

        _seed_order(db, restaurant2, location2)  # Brand B Order — не должен войти

        now = datetime.now(timezone.utc)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

        result = db.execute(
            text("""
                SELECT COUNT(*) AS cnt
                FROM orders
                WHERE restaurant_id = :rid
                  AND created_at >= :start
            """),
            {"rid": restaurant.id, "start": month_start},
        ).scalar()

        assert result == 0, (
            f"M2 FAIL: Quota counted {result} orders for Brand A "
            f"but Brand A has no orders — Brand B order leaked"
        )


# ══════════════════════════════════════════════════════════════════════════════
# N. СОЗДАНИЕ ORDER: restaurant_id == location.restaurant_id
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckN:
    """
    N. При создании Order через create_order:
       order.restaurant_id == location.restaurant_id — всегда.

    Это гарантирует consistency до Migration 0015.
    """

    def test_create_order_restaurant_id_equals_location_restaurant_id(
        self, client, db, product, restaurant, location
    ):
        """N. order.restaurant_id == location.restaurant_id после create_order."""
        resp = client.post("/api/orders/", json={
            "order_type": "takeaway",
            "items": [{"product_id": product.id, "quantity": 1}],
        })
        assert resp.status_code == 201
        order_id = resp.json()["id"]

        order = db.query(Order).filter(Order.id == order_id).first()
        assert order.restaurant_id == location.restaurant_id, (
            f"N FAIL: order.restaurant_id={order.restaurant_id} "
            f"!= location.restaurant_id={location.restaurant_id}"
        )
        # И location_id корректен
        assert order.location_id == location.id


# ══════════════════════════════════════════════════════════════════════════════
# MODEL METADATA CHECKS
# ══════════════════════════════════════════════════════════════════════════════

class TestModelMetadata:
    """Проверки метаданных модели Order."""

    def test_order_has_location_relationship(self):
        """Order.location relationship существует в модели."""
        assert hasattr(Order, "location"), (
            "Order model must have 'location' relationship (S1-3)"
        )

    def test_order_has_location_id_column(self):
        """Order.location_id column присутствует."""
        assert "location_id" in Order.__table__.c, (
            "Order.__table__ must have location_id column"
        )

    def test_order_restaurant_id_still_present(self):
        """restaurant_id не удалён (остаётся до Migration 0015)."""
        assert "restaurant_id" in Order.__table__.c, (
            "restaurant_id must NOT be dropped — Migration 0015 is separate"
        )

    def test_location_id_has_index(self):
        """ix_orders_location_id индекс присутствует в модели."""
        index_names = {idx.name for idx in Order.__table__.indexes}
        assert "ix_orders_location_id" in index_names, (
            f"ix_orders_location_id index missing. Available: {index_names}"
        )
