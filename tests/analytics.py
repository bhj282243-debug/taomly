"""
tests/test_analytics.py — Analytics Dashboard тесты (Stage 2, Sprint 2)

Проверяет:
  - Все 5 endpoints аналитики доступны и возвращают корректную структуру
  - Все периоды (today, 7d, 30d, 90d, this_month) принимаются
  - Недопустимый период → 400
  - Без авторизации → 403/401
  - Тендант-изоляция: ресторан видит только свои данные
  - Пустые данные возвращают нули, а не 500
"""

from datetime import datetime, timezone, timedelta

import pytest

from models import Order, OrderItem


# ──────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────

def _make_order(db, restaurant, status="completed", total=25000, days_ago=0):
    """Создаёт тестовый заказ с одним позицией."""
    created = datetime.now(tz=timezone.utc) - timedelta(days=days_ago, hours=1)
    order = Order(
        restaurant_id=restaurant.id,
        client_telegram_id=111222333,
        client_name="Test Client",
        client_phone="+998901234567",
        order_type="dine_in",
        total_amount=total,
        status=status,
        created_at=created,
        updated_at=created,
    )
    db.add(order)
    db.flush()

    item = OrderItem(
        order_id=order.id,
        name="Самса",
        price=total,
        quantity=1,
    )
    db.add(item)
    db.flush()
    return order


# ──────────────────────────────────────────
# SUMMARY ENDPOINT
# ──────────────────────────────────────────

class TestSummary:
    def test_summary_empty(self, client, auth_headers_restaurant):
        """Пустая БД → нули, не 500."""
        r = client.get("/api/analytics/summary?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert data["revenue"] == 0
        assert data["orders_total"] == 0
        assert data["orders_completed"] == 0
        assert data["orders_cancelled"] == 0
        assert data["avg_check"] == 0
        assert data["returning_clients"] == 0
        assert data["new_clients"] == 0

    def test_summary_with_orders(self, client, db, restaurant, auth_headers_restaurant):
        """Заказы за период — счётчики корректны."""
        _make_order(db, restaurant, status="completed", total=30000, days_ago=1)
        _make_order(db, restaurant, status="completed", total=20000, days_ago=2)
        _make_order(db, restaurant, status="cancelled",  total=10000, days_ago=1)

        r = client.get("/api/analytics/summary?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert data["orders_total"] == 3
        assert data["orders_completed"] == 2
        assert data["orders_cancelled"] == 1
        assert data["revenue"] == 50000
        assert data["avg_check"] == 25000

    def test_summary_invalid_period(self, client, auth_headers_restaurant):
        """Неизвестный период → 400."""
        r = client.get("/api/analytics/summary?period=invalid", headers=auth_headers_restaurant)
        assert r.status_code == 400

    def test_summary_no_auth(self, client):
        """Без токена → 401/403."""
        r = client.get("/api/analytics/summary?period=30d")
        assert r.status_code in (401, 403)

    @pytest.mark.parametrize("period", ["today", "7d", "30d", "90d", "this_month"])
    def test_summary_all_periods_accepted(self, client, auth_headers_restaurant, period):
        """Все допустимые периоды возвращают 200."""
        r = client.get(f"/api/analytics/summary?period={period}", headers=auth_headers_restaurant)
        assert r.status_code == 200

    def test_summary_returning_clients(self, client, db, restaurant, auth_headers_restaurant):
        """Клиент с 2+ заказами считается повторным."""
        for _ in range(3):
            o = Order(
                restaurant_id=restaurant.id,
                client_telegram_id=999888777,
                client_name="Regular",
                client_phone="+998900000001",
                order_type="takeaway",
                total_amount=15000,
                status="completed",
            )
            db.add(o)
        # Второй клиент — только 1 заказ
        o2 = Order(
            restaurant_id=restaurant.id,
            client_telegram_id=111000222,
            client_name="New",
            client_phone="+998900000002",
            order_type="takeaway",
            total_amount=15000,
            status="completed",
        )
        db.add(o2)
        db.flush()

        r = client.get("/api/analytics/summary?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert data["returning_clients"] == 1
        assert data["new_clients"] == 1


# ──────────────────────────────────────────
# REVENUE BY DAY
# ──────────────────────────────────────────

class TestRevenueByDay:
    def test_revenue_by_day_empty(self, client, auth_headers_restaurant):
        r = client.get("/api/analytics/revenue-by-day?period=7d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_revenue_by_day_has_entries(self, client, db, restaurant, auth_headers_restaurant):
        _make_order(db, restaurant, status="completed", total=50000, days_ago=1)
        r = client.get("/api/analytics/revenue-by-day?period=7d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        entry = data[0]
        assert "date" in entry
        assert "revenue" in entry
        assert "orders" in entry


# ──────────────────────────────────────────
# TOP DISHES
# ──────────────────────────────────────────

class TestTopDishes:
    def test_top_dishes_empty(self, client, auth_headers_restaurant):
        r = client.get("/api/analytics/top-dishes?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        assert r.json() == []

    def test_top_dishes_ranking(self, client, db, restaurant, auth_headers_restaurant):
        """Блюдо с большим qty должно быть выше."""
        order = _make_order(db, restaurant, status="completed", total=60000, days_ago=1)
        # Добавим второй item в тот же заказ
        item2 = OrderItem(order_id=order.id, name="Лагман", price=30000, quantity=2)
        db.add(item2)
        db.flush()

        r = client.get("/api/analytics/top-dishes?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert data[0]["rank"] == 1

    def test_top_dishes_limit(self, client, db, restaurant, auth_headers_restaurant):
        """Параметр limit работает."""
        for i in range(5):
            o = _make_order(db, restaurant, status="completed", total=10000, days_ago=1)
            item = OrderItem(order_id=o.id, name=f"Dish{i}", price=10000, quantity=1)
            db.add(item)
        db.flush()

        r = client.get("/api/analytics/top-dishes?period=30d&limit=3", headers=auth_headers_restaurant)
        assert r.status_code == 200
        assert len(r.json()) <= 3


# ──────────────────────────────────────────
# PEAK HOURS
# ──────────────────────────────────────────

class TestPeakHours:
    def test_peak_hours_returns_24(self, client, auth_headers_restaurant):
        """Всегда возвращает 24 элемента (один на каждый час)."""
        r = client.get("/api/analytics/peak-hours?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 24
        hours = [d["hour"] for d in data]
        assert hours == list(range(24))

    def test_peak_hours_structure(self, client, auth_headers_restaurant):
        r = client.get("/api/analytics/peak-hours?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        item = r.json()[0]
        assert "hour" in item
        assert "orders" in item


# ──────────────────────────────────────────
# ORDER TYPES
# ──────────────────────────────────────────

class TestOrderTypes:
    def test_order_types_empty(self, client, auth_headers_restaurant):
        r = client.get("/api/analytics/order-types?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_order_types_structure(self, client, db, restaurant, auth_headers_restaurant):
        _make_order(db, restaurant, status="completed", total=20000, days_ago=1)
        r = client.get("/api/analytics/order-types?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        if data:
            item = data[0]
            assert "order_type" in item
            assert "orders" in item
            assert "revenue" in item


# ──────────────────────────────────────────
# TENANT ISOLATION
# ──────────────────────────────────────────

class TestTenantIsolation:
    def test_restaurant_sees_only_own_orders(self, client, db, restaurant, restaurant2, auth_headers_restaurant):
        """Ресторан 1 не видит заказы ресторана 2."""
        # Заказ для restaurant2
        order_r2 = Order(
            restaurant_id=restaurant2.id,
            client_name="Other",
            client_phone="+998901111111",
            order_type="delivery",
            total_amount=99999,
            status="completed",
        )
        db.add(order_r2)
        db.flush()

        # Смотрим аналитику ресторана 1 — revenue должна быть 0
        r = client.get("/api/analytics/summary?period=30d", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert data["revenue"] == 0
        assert data["orders_total"] == 0
