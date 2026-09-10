"""
modules/cart/tests/test_checkout.py — Taomly Platform
Phase 7: Checkout (Cart → Order) test suite.

Классы:
  TestBasicCheckout        — P: базовый checkout
  TestAvailabilityAtCheckout — Q: свежая валидация при checkout
  TestOrderSnapshot        — R: snapshot имён, цен, валюты
  TestPricingPolicy        — S: server-authoritative цены
  TestCartStatusAfterCheckout — T: статус корзины после checkout
  TestIdempotency          — U: idempotency key семантика
  TestConcurrency          — V: @pytest.mark.postgres, реальный PostgreSQL
  TestTenantIsolation      — W: tenant isolation
  TestTransactionAtomicity — X: атомарность транзакции
  TestStateMachine         — Y: state machine transitions
"""

import threading
from typing import Generator, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api import app
from auth import get_telegram_user
from database import get_db
from models import (
    Location, ModifierGroup, ModifierOption,
    Product, ProductVariant, Restaurant,
)
from models.orders import Order, OrderItem, OrderItemModifier
from modules.cart.models import Cart, CartItem

SESSION_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

CHECKOUT_TAKEAWAY = {"order_type": "takeaway"}
CHECKOUT_DELIVERY = {"order_type": "delivery", "address": "ул. Навои, 1"}


# ──────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────

@pytest.fixture
def checkout_client(db, restaurant, location, tg_user) -> Generator:
    """TestClient с заголовками для cart+checkout (session A)."""
    def override_db():
        yield db

    def override_tg():
        return tg_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_telegram_user] = override_tg

    headers = {
        "X-Restaurant-Id": str(restaurant.id),
        "X-Location-Id":   str(location.id),
        "X-Cart-Session":  SESSION_A,
    }
    with TestClient(app, raise_server_exceptions=True, headers=headers) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def variant(db, product) -> ProductVariant:
    v = ProductVariant(
        product_id=product.id,
        name="Большая порция",
        price=22000,
        is_active=True,
        is_available=True,
        sort_order=1,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def modifier_group(db, product) -> ModifierGroup:
    mg = ModifierGroup(
        product_id=product.id,
        name="Соус",
        is_active=True,
        min_selections=0,
        max_selections=2,
        sort_order=1,
    )
    db.add(mg)
    db.flush()
    return mg


@pytest.fixture
def modifier_opt_a(db, modifier_group) -> ModifierOption:
    opt = ModifierOption(
        modifier_group_id=modifier_group.id,
        name="Острый",
        price_adjustment=1000,
        is_active=True,
        is_available=True,
        sort_order=1,
    )
    db.add(opt)
    db.flush()
    return opt


@pytest.fixture
def seeded_cart(checkout_client, product) -> dict:
    """Добавляет product (qty=2) в корзину."""
    r = checkout_client.post("/api/cart/items", json={
        "product_id": product.id,
        "quantity": 2,
    })
    assert r.status_code == 200, r.text
    return r.json()


# ──────────────────────────────────────────
# P — BASIC CHECKOUT
# ──────────────────────────────────────────

class TestBasicCheckout:
    def test_valid_cart_returns_201(self, checkout_client, product, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["id"] > 0
        assert data["status"] == "accepted"
        assert data["order_type"] == "takeaway"
        assert len(data["items"]) == 1

    def test_empty_cart_returns_422(self, checkout_client):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 422, r.text

    def test_no_active_cart_returns_404(self, checkout_client):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 404, r.text

    def test_already_checked_out_returns_409(self, checkout_client, product, seeded_cart):
        r1 = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r1.status_code == 201
        r2 = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r2.status_code == 409, r2.text

    def test_delivery_without_address_returns_422(self, checkout_client, product, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json={"order_type": "delivery"})
        assert r.status_code == 422

    def test_delivery_with_address_returns_201(self, checkout_client, product, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_DELIVERY)
        assert r.status_code == 201, r.text

    def test_dine_in_without_table_returns_422(self, checkout_client, product, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json={"order_type": "dine_in"})
        assert r.status_code == 422

    def test_dine_in_with_table_returns_201(self, checkout_client, product, seeded_cart, table):
        r = checkout_client.post("/api/cart/checkout", json={
            "order_type": "dine_in", "table_id": table.id
        })
        assert r.status_code == 201, r.text


# ──────────────────────────────────────────
# Q — AVAILABILITY AT CHECKOUT
# ──────────────────────────────────────────

class TestAvailabilityAtCheckout:
    def test_product_unavailable_422(self, checkout_client, product, db, seeded_cart):
        product.is_available = False
        db.flush()
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 422, r.text

    def test_availability_failure_cart_remains_active(
        self, checkout_client, product, db, seeded_cart
    ):
        product.is_available = False
        db.flush()
        checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        db.expire_all()
        cart = db.query(Cart).filter(Cart.session_id == SESSION_A).first()
        assert cart is not None
        assert cart.status == "active"

    def test_availability_failure_no_order_created(
        self, checkout_client, product, db, seeded_cart
    ):
        count_before = db.query(Order).filter(
            Order.restaurant_id == product.restaurant_id
        ).count()
        product.is_available = False
        db.flush()
        checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        count_after = db.query(Order).filter(
            Order.restaurant_id == product.restaurant_id
        ).count()
        assert count_after == count_before

    def test_availability_failure_cart_items_intact(
        self, checkout_client, product, db, seeded_cart
    ):
        cart = db.query(Cart).filter(Cart.session_id == SESSION_A).first()
        items_before = db.query(CartItem).filter(CartItem.cart_id == cart.id).count()
        product.is_available = False
        db.flush()
        checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        db.expire_all()
        items_after = db.query(CartItem).filter(CartItem.cart_id == cart.id).count()
        assert items_after == items_before

    def test_variant_unavailable_422(self, checkout_client, product, variant, db):
        product.price = None
        db.flush()
        checkout_client.post("/api/cart/items", json={
            "product_id": product.id, "variant_id": variant.id, "quantity": 1,
        })
        variant.is_available = False
        db.flush()
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 422, r.text

    def test_modifier_unavailable_422(
        self, checkout_client, product, modifier_group, modifier_opt_a, db
    ):
        checkout_client.post("/api/cart/items", json={
            "product_id": product.id,
            "quantity": 1,
            "modifier_option_ids": [modifier_opt_a.id],
        })
        modifier_opt_a.is_available = False
        db.flush()
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 422, r.text


# ──────────────────────────────────────────
# R — ORDER SNAPSHOT
# ──────────────────────────────────────────

class TestOrderSnapshot:
    def test_snapshot_product_name(self, checkout_client, product, db, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["items"][0]["name"] == product.name

    def test_snapshot_product_name_immutable_after_rename(
        self, checkout_client, product, db, seeded_cart
    ):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        order_id = r.json()["id"]
        original_name = product.name
        product.name = "Переименованный продукт"
        db.flush()
        db.expire_all()
        order_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).first()
        assert order_item.name == original_name

    def test_snapshot_no_variant_name_is_null(self, checkout_client, product, db, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["items"][0]["variant_name"] is None

    def test_snapshot_variant_name(self, checkout_client, product, variant, db):
        product.price = None
        db.flush()
        checkout_client.post("/api/cart/items", json={
            "product_id": product.id, "variant_id": variant.id, "quantity": 1,
        })
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201, r.text
        assert r.json()["items"][0]["variant_name"] == variant.name

    def test_snapshot_modifier_name_and_price(
        self, checkout_client, product, modifier_group, modifier_opt_a, db
    ):
        checkout_client.post("/api/cart/items", json={
            "product_id": product.id, "quantity": 1,
            "modifier_option_ids": [modifier_opt_a.id],
        })
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201, r.text
        order_id = r.json()["id"]
        db.expire_all()
        order_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).first()
        mods = order_item.selected_modifiers
        assert len(mods) == 1
        assert mods[0].name == modifier_opt_a.name
        assert mods[0].price_adjustment == modifier_opt_a.price_adjustment

    def test_snapshot_unit_price(self, checkout_client, product, db, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["items"][0]["price"] == product.price

    def test_snapshot_quantity(self, checkout_client, product, db, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["items"][0]["quantity"] == 2

    def test_snapshot_currency(self, checkout_client, location, product, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["currency"] == location.currency


# ──────────────────────────────────────────
# S — PRICING POLICY
# ──────────────────────────────────────────

class TestPricingPolicy:
    def test_total_amount_server_computed(self, checkout_client, product, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["total_amount"] == product.price * 2

    def test_price_snapshot_used_not_current_price(
        self, checkout_client, product, db, seeded_cart
    ):
        """Цена изменена после add → Order использует старый CartItem.unit_price."""
        original_price = product.price
        product.price = 99999
        db.flush()
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["items"][0]["price"] == original_price
        assert r.json()["total_amount"] == original_price * 2

    def test_two_items_total_sum(self, checkout_client, product, product2, db):
        checkout_client.post("/api/cart/items", json={"product_id": product.id, "quantity": 1})
        checkout_client.post("/api/cart/items", json={"product_id": product2.id, "quantity": 3})
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        expected = product.price * 1 + product2.price * 3
        assert r.json()["total_amount"] == expected


# ──────────────────────────────────────────
# T — CART STATUS AFTER CHECKOUT
# ──────────────────────────────────────────

class TestCartStatusAfterCheckout:
    def test_cart_status_becomes_checked_out(self, checkout_client, product, db, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        db.expire_all()
        cart = db.query(Cart).filter(Cart.session_id == SESSION_A).first()
        assert cart.status == "checked_out"

    def test_get_cart_returns_empty_after_checkout(self, checkout_client, product, seeded_cart):
        checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        r = checkout_client.get("/api/cart")
        assert r.status_code == 200
        data = r.json()
        assert data["cart_id"] == 0
        assert data["items"] == []

    def test_cart_items_preserved_after_checkout(
        self, checkout_client, product, db, seeded_cart
    ):
        cart = db.query(Cart).filter(Cart.session_id == SESSION_A).first()
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        db.expire_all()
        items = db.query(CartItem).filter(CartItem.cart_id == cart.id).all()
        assert len(items) > 0


# ──────────────────────────────────────────
# U — IDEMPOTENCY
# ──────────────────────────────────────────

class TestIdempotency:
    def test_same_key_twice_returns_same_order(self, checkout_client, product, db, seeded_cart):
        key = "test-idempotency-key-001"
        body = {**CHECKOUT_TAKEAWAY, "idempotency_key": key}
        r1 = checkout_client.post("/api/cart/checkout", json=body)
        assert r1.status_code == 201
        r2 = checkout_client.post("/api/cart/checkout", json=body)
        assert r2.status_code == 201
        assert r1.json()["id"] == r2.json()["id"]

    def test_same_key_no_duplicate_in_db(self, checkout_client, product, db, seeded_cart):
        key = "test-idempotency-key-002"
        body = {**CHECKOUT_TAKEAWAY, "idempotency_key": key}
        checkout_client.post("/api/cart/checkout", json=body)
        count_before = db.query(Order).filter(
            Order.restaurant_id == product.restaurant_id
        ).count()
        checkout_client.post("/api/cart/checkout", json=body)
        count_after = db.query(Order).filter(
            Order.restaurant_id == product.restaurant_id
        ).count()
        assert count_after == count_before

    def test_different_key_returns_409(self, checkout_client, product, db, seeded_cart):
        checkout_client.post("/api/cart/checkout", json={
            **CHECKOUT_TAKEAWAY, "idempotency_key": "key-X"
        })
        r2 = checkout_client.post("/api/cart/checkout", json={
            **CHECKOUT_TAKEAWAY, "idempotency_key": "key-Y"
        })
        assert r2.status_code == 409

    def test_no_key_after_checkout_returns_409(self, checkout_client, product, seeded_cart):
        checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        r2 = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r2.status_code == 409

    def test_idempotency_key_stored_in_cart(self, checkout_client, product, db, seeded_cart):
        key = "stored-key-test-003"
        checkout_client.post("/api/cart/checkout", json={
            **CHECKOUT_TAKEAWAY, "idempotency_key": key
        })
        db.expire_all()
        cart = db.query(Cart).filter(Cart.session_id == SESSION_A).first()
        assert cart.checkout_idempotency_key == key

    def test_no_key_leaves_null_in_cart(self, checkout_client, product, db, seeded_cart):
        checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        db.expire_all()
        cart = db.query(Cart).filter(Cart.session_id == SESSION_A).first()
        assert cart.checkout_idempotency_key is None


# ──────────────────────────────────────────
# V — CONCURRENCY (только PostgreSQL)
# ──────────────────────────────────────────

@pytest.mark.postgres
class TestConcurrency:
    """
    Используют реальные независимые PostgreSQL sessions.
    Данные commit'ятся до старта потоков.
    """

    def _make_data(self, suffix: str) -> dict:
        from auth import hash_password
        from database import SessionLocal
        from models import Agency, Category, Location, Product, Restaurant

        s = SessionLocal()
        try:
            agency = Agency(
                name=f"ConcAgency-{suffix}",
                owner_email=f"conc-{suffix}@test.uz",
                owner_password_hash=hash_password("pw"),
            )
            s.add(agency)
            s.flush()

            rest = Restaurant(
                agency_id=agency.id,
                name=f"ConcRest-{suffix}",
                slug=f"conc-rest-{suffix}",
                admin_password_hash=hash_password("pw"),
                currency="UZS",
                telegram_bot_token_encrypted="stub",
                telegram_dispatcher_id=0,
            )
            s.add(rest)
            s.flush()

            loc = Location(
                restaurant_id=rest.id,
                name=f"ConcLoc-{suffix}",
                slug=f"conc-loc-{suffix}",
                is_active=True,
                timezone="Asia/Tashkent",
                delivery_fee=0,
                min_order_amount=0,
                currency="UZS",
                language="uz",
                is_waiter_call_enabled=False,
            )
            s.add(loc)
            s.flush()

            cat = Category(restaurant_id=rest.id, name="Cat", sort_order=1)
            s.add(cat)
            s.flush()

            prod = Product(
                restaurant_id=rest.id,
                category_id=cat.id,
                name=f"Prod-{suffix}",
                price=15000,
                is_available=True,
            )
            s.add(prod)
            s.flush()
            s.commit()

            return {
                "restaurant_id": rest.id,
                "location_id": loc.id,
                "product_id": prod.id,
            }
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def _seed_cart(self, data: dict, session_id: str) -> None:
        from database import SessionLocal
        from models import Location as Loc
        from modules.cart import service as svc

        s = SessionLocal()
        try:
            loc = s.query(Loc).filter(Loc.id == data["location_id"]).first()
            cart = svc.get_or_create_cart(
                db=s, restaurant_id=data["restaurant_id"],
                session_id=session_id, telegram_id=None, currency="UZS",
            )
            svc.add_item(
                db=s, cart=cart,
                product_id=data["product_id"], variant_id=None,
                modifier_option_ids=[], notes=None, quantity=1, location=loc,
            )
        finally:
            s.close()

    def _do_checkout(self, data: dict, session_id: str, key: Optional[str]) -> dict:
        from database import SessionLocal
        from models import Location as Loc
        from modules.cart import service as svc

        s = SessionLocal()
        try:
            loc = s.query(Loc).filter(Loc.id == data["location_id"]).first()
            order = svc.checkout_cart(
                db=s, session_id=session_id,
                restaurant_id=data["restaurant_id"],
                location=loc, telegram_id=None,
                order_type="takeaway", client_name="Test",
                client_phone=None, address=None, table_id=None,
                comment=None, idempotency_key=key, display_name="Test",
            )
            return {"success": True, "order_id": order.id, "status_code": 201}
        except Exception as exc:
            return {
                "success": False, "order_id": None,
                "status_code": getattr(exc, "status_code", 500),
                "error": str(exc),
            }
        finally:
            s.close()

    def test_e_checkout_twice_one_order(self):
        """checkout × 2 одновременно → ровно 1 Order."""
        from database import SessionLocal
        sid = "conc-e-aaaa-0000-0000-000000000001"
        data = self._make_data("e1")
        self._seed_cart(data, sid)

        results = []

        def worker():
            results.append(self._do_checkout(data, sid, None))

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()

        successes = [r for r in results if r["success"]]
        conflicts = [r for r in results if r["status_code"] == 409]
        assert len(successes) == 1, f"Expected 1 success: {results}"
        assert len(conflicts) == 1, f"Expected 1 conflict: {results}"

        with SessionLocal() as s:
            count = s.query(Order).filter(
                Order.restaurant_id == data["restaurant_id"]
            ).count()
        assert count == 1

    def test_a_checkout_plus_add_item_no_partial_state(self):
        """checkout + add_item → сериализованы, нет partial state."""
        from database import SessionLocal
        from models import Location as Loc
        from modules.cart import service as svc
        from modules.cart.models import Cart as CartM

        sid = "conc-a-bbbb-0000-0000-000000000002"
        data = self._make_data("a1")
        self._seed_cart(data, sid)

        results_co = []
        results_add = []

        def do_checkout():
            results_co.append(self._do_checkout(data, sid, None))

        def do_add():
            s = SessionLocal()
            try:
                loc = s.query(Loc).filter(Loc.id == data["location_id"]).first()
                cart = s.query(CartM).filter(
                    CartM.session_id == sid,
                    CartM.restaurant_id == data["restaurant_id"],
                ).first()
                if cart is None:
                    results_add.append({"success": False, "error": "no cart"})
                    return
                svc.add_item(
                    db=s, cart=cart,
                    product_id=data["product_id"], variant_id=None,
                    modifier_option_ids=[], notes=None, quantity=1, location=loc,
                )
                results_add.append({"success": True})
            except Exception as exc:
                results_add.append({"success": False, "error": str(exc)})
            finally:
                s.close()

        t1 = threading.Thread(target=do_checkout)
        t2 = threading.Thread(target=do_add)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert results_co[0]["success"], f"Checkout failed: {results_co}"
        # add_item либо успешно (до lock checkout), либо 409 (после)
        assert "500" not in str(results_add[0].get("error", ""))

        with SessionLocal() as s:
            count = s.query(Order).filter(
                Order.restaurant_id == data["restaurant_id"]
            ).count()
        assert count == 1

    def test_d_checkout_plus_clear_no_empty_order(self):
        """checkout + clear_cart → Order never created with 0 items."""
        from database import SessionLocal
        from modules.cart import service as svc
        from modules.cart.models import Cart as CartM

        sid = "conc-d-cccc-0000-0000-000000000003"
        data = self._make_data("d1")
        self._seed_cart(data, sid)

        results_co = []
        results_cl = []

        def do_checkout():
            results_co.append(self._do_checkout(data, sid, None))

        def do_clear():
            s = SessionLocal()
            try:
                cart = s.query(CartM).filter(
                    CartM.session_id == sid,
                    CartM.restaurant_id == data["restaurant_id"],
                ).first()
                if cart is None:
                    results_cl.append({"success": False, "error": "no cart"})
                    return
                svc.clear_cart(s, cart)
                results_cl.append({"success": True})
            except Exception as exc:
                results_cl.append({"success": False, "error": str(exc)})
            finally:
                s.close()

        t1 = threading.Thread(target=do_checkout)
        t2 = threading.Thread(target=do_clear)
        t1.start(); t2.start()
        t1.join(); t2.join()

        # КРИТИЧНО: никакого Order с 0 items
        with SessionLocal() as s:
            orders = s.query(Order).filter(
                Order.restaurant_id == data["restaurant_id"]
            ).all()
            for o in orders:
                items = s.query(OrderItem).filter(OrderItem.order_id == o.id).all()
                assert len(items) > 0, "Order создан с 0 items — ИНВАРИАНТ НАРУШЕН"


# ──────────────────────────────────────────
# W — TENANT ISOLATION
# ──────────────────────────────────────────

class TestTenantIsolation:
    def test_order_restaurant_id_matches_context(
        self, checkout_client, product, restaurant, seeded_cart
    ):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["restaurant_id"] == restaurant.id

    def test_order_location_id_matches_header(
        self, checkout_client, product, location, seeded_cart
    ):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["location_id"] == location.id


# ──────────────────────────────────────────
# X — TRANSACTION ATOMICITY
# ──────────────────────────────────────────

class TestTransactionAtomicity:
    def test_no_partial_order_on_422(self, checkout_client, product, product2, db):
        """Второй продукт недоступен → rollback, 0 Orders."""
        checkout_client.post("/api/cart/items", json={"product_id": product.id, "quantity": 1})
        checkout_client.post("/api/cart/items", json={"product_id": product2.id, "quantity": 1})
        count_before = db.query(Order).filter(
            Order.restaurant_id == product.restaurant_id
        ).count()
        product2.is_available = False
        db.flush()
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 422
        count_after = db.query(Order).filter(
            Order.restaurant_id == product.restaurant_id
        ).count()
        assert count_after == count_before

    def test_cart_active_on_failure(self, checkout_client, product, db, seeded_cart):
        product.is_available = False
        db.flush()
        checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        db.expire_all()
        cart = db.query(Cart).filter(Cart.session_id == SESSION_A).first()
        assert cart.status == "active"


# ──────────────────────────────────────────
# Y — STATE MACHINE
# ──────────────────────────────────────────

class TestStateMachine:
    def _checkout(self, checkout_client, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        return r.json()["id"]

    def test_checkout_creates_accepted_order(self, checkout_client, product, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert r.json()["status"] == "accepted"

    def test_currency_in_response(self, checkout_client, product, location, seeded_cart):
        r = checkout_client.post("/api/cart/checkout", json=CHECKOUT_TAKEAWAY)
        assert r.status_code == 201
        assert "currency" in r.json()
        assert r.json()["currency"] == location.currency

    def test_valid_transition_to_preparing(self, checkout_client, product, seeded_cart, client):
        order_id = self._checkout(checkout_client, seeded_cart)
        r = client.patch(f"/api/orders/{order_id}/status", json={"status": "preparing"})
        assert r.status_code == 200
        assert r.json()["status"] == "preparing"

    def test_invalid_transition_to_completed(self, checkout_client, product, seeded_cart, client):
        order_id = self._checkout(checkout_client, seeded_cart)
        r = client.patch(f"/api/orders/{order_id}/status", json={"status": "completed"})
        assert r.status_code in (400, 422)

    def test_invalid_transition_to_new(self, checkout_client, product, seeded_cart, client):
        order_id = self._checkout(checkout_client, seeded_cart)
        r = client.patch(f"/api/orders/{order_id}/status", json={"status": "new"})
        assert r.status_code in (400, 422)

    def test_valid_cancel_from_accepted(self, checkout_client, product, seeded_cart, client):
        order_id = self._checkout(checkout_client, seeded_cart)
        r = client.patch(f"/api/orders/{order_id}/status", json={"status": "cancelled"})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

    def test_cancelled_is_terminal(self, checkout_client, product, seeded_cart, client):
        order_id = self._checkout(checkout_client, seeded_cart)
        client.patch(f"/api/orders/{order_id}/status", json={"status": "cancelled"})
        r = client.patch(f"/api/orders/{order_id}/status", json={"status": "accepted"})
        assert r.status_code in (400, 422)
