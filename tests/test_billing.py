"""
tests/test_billing.py — Billing System тесты (Stage 2, Sprint 2)

Проверяет:
  GET  /api/billing/plans              — список планов из БД
  GET  /api/billing/subscription       — текущая подписка (default Free)
  POST /api/billing/subscribe/{id}     — смена плана
  GET  /api/billing/usage              — использование ресурсов
  GET  /api/billing/invoice/{month}    — PDF генерация
  Тендант-изоляция
"""
import pytest
from models import Order, Subscription, SubscriptionPlan


# ──────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────

def _seed_plans(db):
    """Создаёт три плана в БД (как миграция)."""
    if db.query(SubscriptionPlan).count() > 0:
        return
    for p in [
        SubscriptionPlan(id=1, name="Free",  price=0,  currency="USD", orders_per_month=100,  products_limit=20,  description="Bepul tarif"),
        SubscriptionPlan(id=2, name="Basic", price=29, currency="USD", orders_per_month=1000, products_limit=100, description="Basic tarif"),
        SubscriptionPlan(id=3, name="Pro",   price=79, currency="USD", orders_per_month=-1,   products_limit=-1,  description="Pro tarif"),
    ]:
        db.add(p)
    db.flush()


def _seed_subscription(db, restaurant_id, plan_id):
    db.add(Subscription(restaurant_id=restaurant_id, plan_id=plan_id, is_active=True))
    db.flush()


def _seed_order(db, restaurant):
    o = Order(
        restaurant_id=restaurant.id,
        client_name="Test", client_phone="+998901111111",
        order_type="dine_in", total_amount=25000, status="completed",
    )
    db.add(o)
    db.flush()
    return o


# ──────────────────────────────────────────
# PLANS
# ──────────────────────────────────────────

class TestPlans:
    def test_plans_returns_list(self, client, db):
        _seed_plans(db)
        r = client.get("/api/billing/plans")
        assert r.status_code == 200
        assert len(r.json()) == 3

    def test_plans_structure(self, client, db):
        _seed_plans(db)
        plan = client.get("/api/billing/plans").json()[0]
        for field in ("id", "name", "price", "currency", "orders_per_month", "products_limit"):
            assert field in plan

    def test_plans_currency_field(self, client, db):
        """Цена хранится отдельно от валюты."""
        _seed_plans(db)
        free = next(p for p in client.get("/api/billing/plans").json() if p["name"] == "Free")
        assert free["price"] == 0
        assert free["currency"] == "USD"

    def test_pro_unlimited(self, client, db):
        _seed_plans(db)
        pro = next(p for p in client.get("/api/billing/plans").json() if p["name"] == "Pro")
        assert pro["orders_per_month"] == -1
        assert pro["products_limit"] == -1

    def test_plans_no_auth_required(self, client, db):
        """Планы публичны — авторизация не нужна."""
        _seed_plans(db)
        assert client.get("/api/billing/plans").status_code == 200


# ──────────────────────────────────────────
# SUBSCRIPTION
# ──────────────────────────────────────────

class TestSubscription:
    def test_default_free_when_no_subscription(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        r = client.get("/api/billing/subscription", headers=auth_headers_restaurant)
        assert r.status_code == 200
        assert r.json()["plan_name"] == "Free"

    def test_subscription_no_auth(self, client):
        assert client.get("/api/billing/subscription").status_code in (401, 403)

    def test_subscription_structure(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        data = client.get("/api/billing/subscription", headers=auth_headers_restaurant).json()
        for field in ("plan_id", "plan_name", "price", "currency", "orders_per_month",
                      "products_limit", "started_at", "expires_at", "is_active"):
            assert field in data

    def test_subscription_after_seeding(self, client, db, restaurant, auth_headers_restaurant):
        _seed_plans(db)
        _seed_subscription(db, restaurant.id, plan_id=2)
        r = client.get("/api/billing/subscription", headers=auth_headers_restaurant)
        assert r.json()["plan_name"] == "Basic"

    def test_expires_at_none_by_default(self, client, db, restaurant, auth_headers_restaurant):
        """expires_at=None означает бессрочно."""
        _seed_plans(db)
        _seed_subscription(db, restaurant.id, plan_id=1)
        data = client.get("/api/billing/subscription", headers=auth_headers_restaurant).json()
        assert data["expires_at"] is None


# ──────────────────────────────────────────
# SUBSCRIBE
# ──────────────────────────────────────────

class TestSubscribe:
    def test_subscribe_basic(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        r = client.post("/api/billing/subscribe/2", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["plan_name"] == "Basic"

    def test_subscribe_pro(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        r = client.post("/api/billing/subscribe/3", headers=auth_headers_restaurant)
        assert r.status_code == 200
        assert r.json()["plan_name"] == "Pro"

    def test_subscribe_nonexistent_plan(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        r = client.post("/api/billing/subscribe/999", headers=auth_headers_restaurant)
        assert r.status_code in (404, 422)

    def test_subscribe_no_auth(self, client, db):
        _seed_plans(db)
        assert client.post("/api/billing/subscribe/2").status_code in (401, 403)

    def test_subscribe_updates_plan(self, client, db, restaurant, auth_headers_restaurant):
        """После смены плана — subscription возвращает новый план."""
        _seed_plans(db)
        client.post("/api/billing/subscribe/2", headers=auth_headers_restaurant)
        r = client.get("/api/billing/subscription", headers=auth_headers_restaurant)
        assert r.json()["plan_id"] == 2

    def test_subscribe_deactivates_old(self, client, db, restaurant, auth_headers_restaurant):
        """Старая подписка деактивируется при смене."""
        _seed_plans(db)
        _seed_subscription(db, restaurant.id, plan_id=1)
        client.post("/api/billing/subscribe/2", headers=auth_headers_restaurant)
        active_count = db.query(Subscription).filter(
            Subscription.restaurant_id == restaurant.id,
            Subscription.is_active == True,
        ).count()
        assert active_count == 1


# ──────────────────────────────────────────
# USAGE
# ──────────────────────────────────────────

class TestUsage:
    def test_usage_empty(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        r = client.get("/api/billing/usage", headers=auth_headers_restaurant)
        assert r.status_code == 200
        data = r.json()
        assert data["orders_used"] == 0
        assert data["products_used"] == 0

    def test_usage_counts_orders(self, client, db, restaurant, auth_headers_restaurant):
        _seed_plans(db)
        _seed_order(db, restaurant)
        _seed_order(db, restaurant)
        r = client.get("/api/billing/usage", headers=auth_headers_restaurant)
        assert r.json()["orders_used"] == 2

    def test_usage_remaining(self, client, db, restaurant, auth_headers_restaurant):
        """orders_remaining = limit - used."""
        _seed_plans(db)
        _seed_subscription(db, restaurant.id, plan_id=1)  # Free: limit=100
        _seed_order(db, restaurant)
        r = client.get("/api/billing/usage", headers=auth_headers_restaurant)
        data = r.json()
        assert data["orders_remaining"] == 99

    def test_usage_unlimited_pro(self, client, db, restaurant, auth_headers_restaurant):
        """Pro план → remaining и pct = -1."""
        _seed_plans(db)
        _seed_subscription(db, restaurant.id, plan_id=3)
        r = client.get("/api/billing/usage", headers=auth_headers_restaurant)
        data = r.json()
        assert data["orders_remaining"] == -1
        assert data["orders_pct"] == -1

    def test_usage_structure(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        data = client.get("/api/billing/usage", headers=auth_headers_restaurant).json()
        for field in ("period", "orders_used", "orders_limit", "orders_remaining",
                      "orders_pct", "products_used", "products_limit",
                      "products_remaining", "products_pct"):
            assert field in data

    def test_usage_no_auth(self, client):
        assert client.get("/api/billing/usage").status_code in (401, 403)


# ──────────────────────────────────────────
# INVOICE
# ──────────────────────────────────────────

class TestInvoice:
    def test_invoice_pdf_or_503(self, client, db, auth_headers_restaurant):
        """200 если reportlab установлен, 503 если нет — оба допустимы в CI."""
        _seed_plans(db)
        r = client.get("/api/billing/invoice/7", headers=auth_headers_restaurant)
        assert r.status_code in (200, 503)

    def test_invoice_pdf_content_type(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        r = client.get("/api/billing/invoice/7", headers=auth_headers_restaurant)
        if r.status_code == 200:
            assert "pdf" in r.headers.get("content-type", "")

    def test_invoice_invalid_month(self, client, db, auth_headers_restaurant):
        _seed_plans(db)
        r = client.get("/api/billing/invoice/13", headers=auth_headers_restaurant)
        assert r.status_code == 422

    def test_invoice_no_auth(self, client):
        assert client.get("/api/billing/invoice/7").status_code in (401, 403)


# ──────────────────────────────────────────
# TENANT ISOLATION
# ──────────────────────────────────────────

class TestTenantIsolation:
    def test_usage_isolated_from_other_restaurant(
        self, client, db, restaurant, restaurant2, auth_headers_restaurant
    ):
        """Ресторан 1 не видит заказы ресторана 2 в usage."""
        _seed_plans(db)
        for _ in range(5):
            db.add(Order(
                restaurant_id=restaurant2.id,
                client_name="Other", client_phone="+998900000000",
                order_type="takeaway", total_amount=10000, status="completed",
            ))
        db.flush()
        r = client.get("/api/billing/usage", headers=auth_headers_restaurant)
        assert r.json()["orders_used"] == 0

    def test_subscription_isolated(
        self, client, db, restaurant, restaurant2, auth_headers_restaurant
    ):
        """Ресторан 1 не видит подписку ресторана 2."""
        _seed_plans(db)
        _seed_subscription(db, restaurant2.id, plan_id=3)  # restaurant2 → Pro
        r = client.get("/api/billing/subscription", headers=auth_headers_restaurant)
        # restaurant1 должен видеть Free (свой дефолт), не Pro
        assert r.json()["plan_name"] == "Free"
