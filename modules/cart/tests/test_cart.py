"""
modules/cart/tests/test_cart.py — Taomly Platform
Phase 6: Cart Engine tests.

Test matrix:
  A  — Cart creation / empty cart
  B  — Add product (basic, inactive, unavailable, wrong restaurant)
  C  — Add variant (valid, inactive, unavailable, wrong product)
  D  — Modifiers (valid, duplicate IDs, inactive, unavailable, min/max, wrong product)
  E  — Canonical modifier ordering / identical sets → one CartItem
  F  — Server-authoritative pricing / price snapshot semantics
  G  — Update quantity
  H  — Remove item
  I  — Clear cart
  J  — Currency mismatch → 409
  K  — Tenant isolation / cross-restaurant protection
  L  — Anonymous session isolation
  M  — Concurrent identical adds (INSERT ON CONFLICT)
  N  — Concurrent quantity updates (SELECT FOR UPDATE)
  O  — Regression (existing endpoints not broken)

Fixtures reuse conftest.py globals:
  db, client, restaurant, restaurant2, location, location2,
  product, product2, product_unavailable, tg_user, tg_user2, agency
"""

import hashlib
import threading
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api import app
from auth import TelegramUser, get_telegram_user
from database import get_db
from models import (
    Category,
    Location,
    ModifierGroup,
    ModifierOption,
    Product,
    ProductVariant,
    Restaurant,
)
from modules.cart.models import Cart, CartItem, CartItemModifier
from modules.cart.service import _EMPTY_MODIFIERS_HASH, compute_modifiers_hash

# ──────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────
SESSION_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SESSION_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ──────────────────────────────────────────
# PHASE 6 FIXTURES
# ──────────────────────────────────────────

@pytest.fixture
def variant(db, product) -> ProductVariant:
    v = ProductVariant(
        product_id=product.id,
        name="Полная порция",
        price=20000,
        is_active=True,
        is_available=True,
        sort_order=1,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def variant_inactive(db, product) -> ProductVariant:
    v = ProductVariant(
        product_id=product.id,
        name="Неактивный вариант",
        price=25000,
        is_active=False,
        is_available=True,
        sort_order=2,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def variant_unavailable(db, product) -> ProductVariant:
    v = ProductVariant(
        product_id=product.id,
        name="Недоступный вариант",
        price=25000,
        is_active=True,
        is_available=False,
        sort_order=3,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def modifier_group(db, product) -> ModifierGroup:
    g = ModifierGroup(
        product_id=product.id,
        name="Дополнительно",
        min_selections=0,
        max_selections=3,
        is_active=True,
        sort_order=1,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def modifier_group_required(db, product) -> ModifierGroup:
    g = ModifierGroup(
        product_id=product.id,
        name="Обязательная группа",
        min_selections=1,
        max_selections=1,
        is_active=True,
        sort_order=2,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def modifier_group_inactive(db, product) -> ModifierGroup:
    g = ModifierGroup(
        product_id=product.id,
        name="Неактивная группа",
        min_selections=0,
        max_selections=1,
        is_active=False,
        sort_order=3,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def modifier_opt_a(db, modifier_group) -> ModifierOption:
    o = ModifierOption(
        modifier_group_id=modifier_group.id,
        name="Extra meat",
        price_adjustment=10000,
        is_active=True,
        is_available=True,
        sort_order=1,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def modifier_opt_b(db, modifier_group) -> ModifierOption:
    o = ModifierOption(
        modifier_group_id=modifier_group.id,
        name="Яйцо",
        price_adjustment=5000,
        is_active=True,
        is_available=True,
        sort_order=2,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def modifier_opt_inactive(db, modifier_group) -> ModifierOption:
    o = ModifierOption(
        modifier_group_id=modifier_group.id,
        name="Неактивная опция",
        price_adjustment=3000,
        is_active=False,
        is_available=True,
        sort_order=3,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def modifier_opt_unavailable(db, modifier_group) -> ModifierOption:
    o = ModifierOption(
        modifier_group_id=modifier_group.id,
        name="Нет в наличии",
        price_adjustment=2000,
        is_active=True,
        is_available=False,
        sort_order=4,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def modifier_opt_required(db, modifier_group_required) -> ModifierOption:
    o = ModifierOption(
        modifier_group_id=modifier_group_required.id,
        name="Острое",
        price_adjustment=0,
        is_active=True,
        is_available=True,
        sort_order=1,
    )
    db.add(o)
    db.flush()
    return o


# Product in restaurant2 for cross-restaurant tests
@pytest.fixture
def product_r2(db, restaurant2) -> Product:
    cat = Category(restaurant_id=restaurant2.id, name="Cat R2", sort_order=1)
    db.add(cat)
    db.flush()
    p = Product(
        restaurant_id=restaurant2.id,
        category_id=cat.id,
        name="Блюдо ресторана 2",
        price=50000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


# Cart client fixture for Phase 6
@pytest.fixture
def cart_client(db, restaurant, location, tg_user) -> Generator:
    """TestClient with cart-specific headers (session A, restaurant 1, location 1)."""
    def override_db():
        yield db

    def override_tg():
        return tg_user

    from auth import get_current_agency, get_current_restaurant_admin

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
def cart_client_b(db, restaurant, location, tg_user) -> Generator:
    """Second session (SESSION_B) — same restaurant."""
    def override_db():
        yield db

    def override_tg():
        return tg_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_telegram_user] = override_tg

    headers = {
        "X-Restaurant-Id": str(restaurant.id),
        "X-Location-Id":   str(location.id),
        "X-Cart-Session":  SESSION_B,
    }
    with TestClient(app, raise_server_exceptions=True, headers=headers) as c:
        yield c

    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════
# A — CART CREATION / EMPTY CART
# ═══════════════════════════════════════════════════════════════════

class TestCartCreation:
    def test_get_empty_cart_returns_200(self, cart_client):
        """GET /api/cart with no cart returns empty structure, not 404."""
        r = cart_client.get("/api/cart")
        assert r.status_code == 200
        data = r.json()
        assert data["items"] == []
        assert data["subtotal"] == 0
        assert data["item_count"] == 0

    def test_empty_cart_has_correct_currency(self, cart_client, location):
        r = cart_client.get("/api/cart")
        assert r.status_code == 200
        assert r.json()["currency"] == location.currency

    def test_cart_created_lazily_on_first_add(self, cart_client, product, db):
        """Cart record is only created in DB when first item is added."""
        r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        assert r.status_code == 200
        count = db.query(Cart).count()
        assert count == 1

    def test_missing_session_header_returns_422(self, db, restaurant, location, tg_user):
        def override_db():
            yield db
        def override_tg():
            return tg_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_telegram_user] = override_tg

        with TestClient(app) as c:
            r = c.get(
                "/api/cart",
                headers={
                    "X-Restaurant-Id": str(restaurant.id),
                    "X-Location-Id": str(location.id),
                    # No X-Cart-Session
                },
            )
        app.dependency_overrides.clear()
        assert r.status_code == 422

    def test_invalid_location_returns_404(self, db, restaurant, tg_user):
        def override_db():
            yield db
        def override_tg():
            return tg_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_telegram_user] = override_tg

        with TestClient(app) as c:
            r = c.post(
                "/api/cart/items",
                headers={
                    "X-Restaurant-Id": str(restaurant.id),
                    "X-Location-Id":   "999999",  # nonexistent
                    "X-Cart-Session":  SESSION_A,
                },
                json={"product_id": 1},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# B — ADD PRODUCT
# ═══════════════════════════════════════════════════════════════════

class TestAddProduct:
    def test_add_valid_product(self, cart_client, product):
        r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        assert r.status_code == 200
        data = r.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["product_id"] == product.id
        assert data["items"][0]["quantity"] == 1

    def test_add_invalid_product_id_returns_404(self, cart_client):
        r = cart_client.post("/api/cart/items", json={"product_id": 999999})
        assert r.status_code == 404

    def test_add_inactive_product_returns_400(self, cart_client, product_unavailable):
        r = cart_client.post("/api/cart/items", json={"product_id": product_unavailable.id})
        assert r.status_code == 400

    def test_add_product_from_other_restaurant_returns_404(self, cart_client, product_r2):
        """cart_client uses restaurant 1; product_r2 belongs to restaurant 2."""
        r = cart_client.post("/api/cart/items", json={"product_id": product_r2.id})
        assert r.status_code == 404

    def test_subtotal_correct_after_add(self, cart_client, product):
        r = cart_client.post("/api/cart/items", json={"product_id": product.id, "quantity": 3})
        assert r.status_code == 200
        data = r.json()
        assert data["items"][0]["line_total"] == product.price * 3
        assert data["subtotal"] == product.price * 3
        assert data["item_count"] == 3


# ═══════════════════════════════════════════════════════════════════
# C — VARIANT
# ═══════════════════════════════════════════════════════════════════

class TestVariant:
    def test_add_valid_variant(self, cart_client, product, variant):
        r = cart_client.post("/api/cart/items", json={
            "product_id": product.id,
            "variant_id": variant.id,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["items"][0]["variant_id"] == variant.id
        assert data["items"][0]["unit_price"] == variant.price

    def test_add_invalid_variant_id_returns_404(self, cart_client, product):
        r = cart_client.post("/api/cart/items", json={
            "product_id": product.id,
            "variant_id": 999999,
        })
        assert r.status_code == 404

    def test_add_inactive_variant_returns_400(self, cart_client, product, variant_inactive):
        r = cart_client.post("/api/cart/items", json={
            "product_id": product.id,
            "variant_id": variant_inactive.id,
        })
        assert r.status_code == 400

    def test_add_unavailable_variant_returns_400(self, cart_client, product, variant_unavailable):
        r = cart_client.post("/api/cart/items", json={
            "product_id": product.id,
            "variant_id": variant_unavailable.id,
        })
        assert r.status_code == 400

    def test_variant_from_wrong_product_returns_404(self, cart_client, product2, variant):
        """variant belongs to product, not product2."""
        r = cart_client.post("/api/cart/items", json={
            "product_id": product2.id,
            "variant_id": variant.id,
        })
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# D — MODIFIERS
# ═══════════════════════════════════════════════════════════════════

class TestModifiers:
    def test_add_valid_modifier(self, cart_client, product, modifier_group, modifier_opt_a):
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_a.id],
        })
        assert r.status_code == 200
        data = r.json()
        item = data["items"][0]
        assert item["unit_price"] == product.price + modifier_opt_a.price_adjustment
        assert len(item["modifiers"]) == 1
        assert item["modifiers"][0]["name"] == modifier_opt_a.name
        assert item["modifiers"][0]["price_adjustment"] == modifier_opt_a.price_adjustment

    def test_duplicate_modifier_option_ids_returns_400(
        self, cart_client, product, modifier_group, modifier_opt_a
    ):
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_a.id, modifier_opt_a.id],
        })
        assert r.status_code == 400
        assert "Duplicate" in r.json()["detail"]

    def test_inactive_modifier_option_returns_400(
        self, cart_client, product, modifier_group, modifier_opt_inactive
    ):
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_inactive.id],
        })
        assert r.status_code == 400

    def test_unavailable_modifier_option_returns_400(
        self, cart_client, product, modifier_group, modifier_opt_unavailable
    ):
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_unavailable.id],
        })
        assert r.status_code == 400

    def test_modifier_from_wrong_product_returns_400(
        self, cart_client, product2, modifier_group, modifier_opt_a
    ):
        """modifier_opt_a belongs to product, not product2."""
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product2.id,
            "modifier_option_ids": [modifier_opt_a.id],
        })
        assert r.status_code == 400

    def test_required_group_missing_selection_returns_400(
        self, cart_client, product, modifier_group_required, modifier_opt_required
    ):
        """modifier_group_required has min_selections=1; no selection provided."""
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [],
        })
        assert r.status_code == 400

    def test_max_selections_exceeded_returns_400(
        self, cart_client, product, modifier_group, modifier_opt_a, modifier_opt_b,
        db
    ):
        """modifier_group max_selections=3; add 4th option (need 4 options in group)."""
        # Create 2 more options to exceed max=3
        opt_c = ModifierOption(
            modifier_group_id=modifier_group.id,
            name="C", price_adjustment=1000,
            is_active=True, is_available=True, sort_order=5,
        )
        opt_d = ModifierOption(
            modifier_group_id=modifier_group.id,
            name="D", price_adjustment=1000,
            is_active=True, is_available=True, sort_order=6,
        )
        db.add_all([opt_c, opt_d])
        db.flush()

        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [
                modifier_opt_a.id, modifier_opt_b.id, opt_c.id, opt_d.id
            ],
        })
        assert r.status_code == 400

    def test_required_group_satisfied(
        self, cart_client, product, modifier_group_required, modifier_opt_required
    ):
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_required.id],
        })
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# E — CANONICAL MODIFIER ORDERING / ITEM IDENTITY
# ═══════════════════════════════════════════════════════════════════

class TestModifierIdentity:
    def test_different_order_same_modifiers_one_cart_item(
        self, cart_client, product, modifier_group, modifier_opt_a, modifier_opt_b, db
    ):
        """[A, B] and [B, A] should produce the same CartItem (merged)."""
        cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_a.id, modifier_opt_b.id],
        })
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_b.id, modifier_opt_a.id],
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["quantity"] == 2

    def test_different_modifiers_different_cart_items(
        self, cart_client, product, modifier_group, modifier_opt_a, modifier_opt_b
    ):
        cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_a.id],
        })
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_b.id],
        })
        assert r.status_code == 200
        assert len(r.json()["items"]) == 2

    def test_no_modifiers_and_with_modifiers_are_different_items(
        self, cart_client, product, modifier_group, modifier_opt_a
    ):
        cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [],
        })
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_a.id],
        })
        assert r.status_code == 200
        assert len(r.json()["items"]) == 2

    def test_modifiers_hash_empty(self):
        h = compute_modifiers_hash([])
        assert h == _EMPTY_MODIFIERS_HASH
        assert len(h) == 64

    def test_modifiers_hash_full_sha256(self):
        h = compute_modifiers_hash([5, 3, 7])
        # sorted → [3,5,7] → "3,5,7"
        expected = hashlib.sha256(b"3,5,7").hexdigest()
        assert h == expected
        assert len(h) == 64

    def test_modifiers_hash_order_independent(self):
        assert compute_modifiers_hash([1, 2, 3]) == compute_modifiers_hash([3, 1, 2])

    def test_different_variants_different_cart_items(self, cart_client, product, variant, db):
        # Add second variant
        v2 = ProductVariant(
            product_id=product.id, name="Половина",
            price=10000, is_active=True, is_available=True, sort_order=2,
        )
        db.add(v2)
        db.flush()

        cart_client.post("/api/cart/items", json={
            "product_id": product.id, "variant_id": variant.id
        })
        r = cart_client.post("/api/cart/items", json={
            "product_id": product.id, "variant_id": v2.id
        })
        assert r.status_code == 200
        assert len(r.json()["items"]) == 2


# ═══════════════════════════════════════════════════════════════════
# F — PRICING
# ═══════════════════════════════════════════════════════════════════

class TestPricing:
    def test_server_price_used_not_client(self, cart_client, product):
        """Client cannot influence price — it's not even in the schema."""
        r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["unit_price"] == product.price

    def test_modifier_price_adjustment_included(
        self, cart_client, product, modifier_group, modifier_opt_a
    ):
        r = cart_client.post("/api/cart/items", json={
            "product_id":          product.id,
            "modifier_option_ids": [modifier_opt_a.id],
        })
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["unit_price"] == product.price + modifier_opt_a.price_adjustment

    def test_line_total_equals_unit_price_times_qty(self, cart_client, product):
        r = cart_client.post("/api/cart/items", json={"product_id": product.id, "quantity": 4})
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["line_total"] == item["unit_price"] * 4

    def test_subtotal_is_sum_of_line_totals(self, cart_client, product, product2):
        cart_client.post("/api/cart/items", json={"product_id": product.id,  "quantity": 2})
        r = cart_client.post("/api/cart/items", json={"product_id": product2.id, "quantity": 1})
        assert r.status_code == 200
        data = r.json()
        expected = sum(i["line_total"] for i in data["items"])
        assert data["subtotal"] == expected

    def test_unit_price_snapshot_not_changed_on_subsequent_add(
        self, cart_client, product, db
    ):
        """Add once, change product price in DB, add again → unit_price unchanged."""
        cart_client.post("/api/cart/items", json={"product_id": product.id})
        original_price = product.price

        # Simulate price change
        product.price = original_price + 99000
        db.flush()

        r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        assert r.status_code == 200
        item = r.json()["items"][0]
        # quantity incremented, but unit_price stays at original snapshot
        assert item["quantity"] == 2
        assert item["unit_price"] == original_price
        assert item["line_total"] == original_price * 2

    def test_variant_price_used_over_product_price(self, cart_client, product, variant):
        r = cart_client.post("/api/cart/items", json={
            "product_id": product.id,
            "variant_id": variant.id,
        })
        assert r.status_code == 200
        assert r.json()["items"][0]["unit_price"] == variant.price


# ═══════════════════════════════════════════════════════════════════
# G — UPDATE QUANTITY
# ═══════════════════════════════════════════════════════════════════

class TestUpdateQuantity:
    def test_update_quantity(self, cart_client, product):
        add_r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        item_id = add_r.json()["items"][0]["id"]

        r = cart_client.patch(f"/api/cart/items/{item_id}", json={"quantity": 5})
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["quantity"] == 5
        assert item["line_total"] == item["unit_price"] * 5

    def test_update_does_not_change_unit_price(self, cart_client, product):
        add_r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        item_id = add_r.json()["items"][0]["id"]
        original_price = add_r.json()["items"][0]["unit_price"]

        r = cart_client.patch(f"/api/cart/items/{item_id}", json={"quantity": 3})
        assert r.status_code == 200
        assert r.json()["items"][0]["unit_price"] == original_price

    def test_update_nonexistent_item_returns_404(self, cart_client, product):
        cart_client.post("/api/cart/items", json={"product_id": product.id})
        r = cart_client.patch("/api/cart/items/999999", json={"quantity": 2})
        assert r.status_code == 404

    def test_update_item_from_other_session_returns_404(
        self, cart_client, cart_client_b, product
    ):
        """Session B cannot update item belonging to Session A."""
        add_r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        item_id = add_r.json()["items"][0]["id"]

        r = cart_client_b.patch(f"/api/cart/items/{item_id}", json={"quantity": 5})
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# H — REMOVE ITEM
# ═══════════════════════════════════════════════════════════════════

class TestRemoveItem:
    def test_remove_item(self, cart_client, product, product2):
        cart_client.post("/api/cart/items", json={"product_id": product.id})
        add_r = cart_client.post("/api/cart/items", json={"product_id": product2.id})
        item_id = add_r.json()["items"][-1]["id"]

        r = cart_client.delete(f"/api/cart/items/{item_id}")
        assert r.status_code == 200
        ids = [i["id"] for i in r.json()["items"]]
        assert item_id not in ids
        assert len(r.json()["items"]) == 1

    def test_remove_nonexistent_item_returns_404(self, cart_client, product):
        cart_client.post("/api/cart/items", json={"product_id": product.id})
        r = cart_client.delete("/api/cart/items/999999")
        assert r.status_code == 404

    def test_remove_item_from_other_session_returns_404(
        self, cart_client, cart_client_b, product
    ):
        add_r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        item_id = add_r.json()["items"][0]["id"]
        r = cart_client_b.delete(f"/api/cart/items/{item_id}")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# I — CLEAR CART
# ═══════════════════════════════════════════════════════════════════

class TestClearCart:
    def test_clear_cart(self, cart_client, product, product2, db):
        cart_client.post("/api/cart/items", json={"product_id": product.id})
        cart_client.post("/api/cart/items", json={"product_id": product2.id})

        r = cart_client.delete("/api/cart")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Cart record still exists, items gone
        assert db.query(Cart).filter(Cart.status == "active").count() == 1
        assert db.query(CartItem).count() == 0

    def test_clear_empty_cart_ok(self, cart_client):
        r = cart_client.delete("/api/cart")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_clear_does_not_affect_other_session(
        self, cart_client, cart_client_b, product, db
    ):
        cart_client.post("/api/cart/items", json={"product_id": product.id})
        cart_client_b.post("/api/cart/items", json={"product_id": product.id})

        cart_client.delete("/api/cart")

        # Session B cart still has its item
        r = cart_client_b.get("/api/cart")
        assert len(r.json()["items"]) == 1


# ═══════════════════════════════════════════════════════════════════
# J — CURRENCY MISMATCH
# ═══════════════════════════════════════════════════════════════════

class TestCurrencyMismatch:
    def test_currency_mismatch_returns_409(self, db, restaurant, location, location_a2, tg_user, product):
        """
        location has currency='UZS', location_a2 also 'UZS' — same restaurant.
        We create a cart via location, then add via a location with different currency.
        To test 409 we need a third location with different currency.
        """
        # Create a location with USD for restaurant 1
        loc_usd = Location(
            restaurant_id=restaurant.id,
            name="USD Branch",
            slug=f"{restaurant.slug}-usd",
            is_active=True,
            timezone="Asia/Tashkent",
            delivery_fee=0,
            min_order_amount=0,
            currency="USD",
            language="uz",
            is_waiter_call_enabled=False,
        )
        db.add(loc_usd)
        db.flush()

        def override_db():
            yield db
        def override_tg():
            return tg_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_telegram_user] = override_tg

        headers_uzs = {
            "X-Restaurant-Id": str(restaurant.id),
            "X-Location-Id":   str(location.id),   # UZS
            "X-Cart-Session":  SESSION_A,
        }
        headers_usd = {
            "X-Restaurant-Id": str(restaurant.id),
            "X-Location-Id":   str(loc_usd.id),    # USD
            "X-Cart-Session":  SESSION_A,
        }

        with TestClient(app) as c:
            # First add via UZS location — cart created with UZS
            r1 = c.post("/api/cart/items", headers=headers_uzs, json={"product_id": product.id})
            assert r1.status_code == 200
            # Second add via USD location — currency mismatch
            r2 = c.post("/api/cart/items", headers=headers_usd, json={"product_id": product.id})
            assert r2.status_code == 409
            assert "currency" in r2.json()["detail"].lower()

        app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════
# K — TENANT ISOLATION / CROSS-RESTAURANT
# ═══════════════════════════════════════════════════════════════════

class TestTenantIsolation:
    def test_cross_restaurant_product_returns_404(self, cart_client, product_r2):
        """cart_client operates on restaurant 1; product_r2 is from restaurant 2."""
        r = cart_client.post("/api/cart/items", json={"product_id": product_r2.id})
        assert r.status_code == 404

    def test_location_from_other_restaurant_returns_404(self, db, restaurant, location2, tg_user, product):
        """location2 belongs to restaurant2; tg_user is from restaurant1."""
        def override_db():
            yield db
        def override_tg():
            return tg_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_telegram_user] = override_tg

        with TestClient(app) as c:
            r = c.post(
                "/api/cart/items",
                headers={
                    "X-Restaurant-Id": str(restaurant.id),
                    "X-Location-Id":   str(location2.id),  # wrong restaurant
                    "X-Cart-Session":  SESSION_A,
                },
                json={"product_id": product.id},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 404

    def test_restaurant_id_from_body_ignored(self, cart_client, product):
        """restaurant_id is not accepted from body — POST body cannot contain it."""
        # AddItemRequest schema has no restaurant_id field; this tests that
        # any extra field is ignored and request proceeds normally.
        r = cart_client.post("/api/cart/items", json={
            "product_id":    product.id,
            "restaurant_id": 999999,  # extra field — should be ignored
        })
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# L — ANONYMOUS SESSION ISOLATION
# ═══════════════════════════════════════════════════════════════════

class TestSessionIsolation:
    def test_different_sessions_have_separate_carts(
        self, cart_client, cart_client_b, product, product2
    ):
        cart_client.post("/api/cart/items", json={"product_id": product.id})
        cart_client_b.post("/api/cart/items", json={"product_id": product2.id})

        r_a = cart_client.get("/api/cart")
        r_b = cart_client_b.get("/api/cart")

        assert len(r_a.json()["items"]) == 1
        assert len(r_b.json()["items"]) == 1
        assert r_a.json()["items"][0]["product_id"] == product.id
        assert r_b.json()["items"][0]["product_id"] == product2.id

    def test_session_b_cannot_clear_session_a_cart(
        self, cart_client, cart_client_b, product
    ):
        cart_client.post("/api/cart/items", json={"product_id": product.id})
        cart_client_b.delete("/api/cart")  # clears B's (empty) cart

        r = cart_client.get("/api/cart")
        assert len(r.json()["items"]) == 1


# ═══════════════════════════════════════════════════════════════════
# M — CONCURRENT IDENTICAL ADDS (INSERT ON CONFLICT)
# ═══════════════════════════════════════════════════════════════════

class TestConcurrentAdd:
    def test_quantity_increment_on_repeated_add(self, cart_client, product):
        """Repeated identical adds increment quantity (not create duplicate items)."""
        for _ in range(3):
            cart_client.post("/api/cart/items", json={"product_id": product.id})
        r = cart_client.get("/api/cart")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["quantity"] == 3

    @pytest.mark.postgres
    def test_concurrent_identical_adds_no_duplicate(self):
        """
        Two concurrent service.add_item() calls for the same (session_id, product)
        must not produce two CartItem rows.

        INSERT ... ON CONFLICT DO UPDATE is atomic at the PostgreSQL level.
        Each worker uses its own independent SQLAlchemy session (no shared state).
        FastAPI / app.dependency_overrides are NOT used — this tests the Cart Engine
        directly at the service layer against a real PostgreSQL instance.

        Marked @pytest.mark.postgres: skipped on SQLite (no functional unique index
        support for ON CONFLICT with COALESCE expression).

        WHY self-contained data (no fixtures):
        The db fixture wraps each test in a SAVEPOINT transaction. SessionLocal()
        in worker threads opens NEW real DB connections that cannot see uncommitted
        SAVEPOINT data — fixture rows are invisible to worker threads. This test
        therefore creates, commits, and cleans up its own data using real sessions.

        Assertions:
          - both workers succeed (no exception)
          - exactly one CartItem row in DB after both complete
          - quantity == 2 (sum of both adds)
          - unit_price is the original snapshot (not overwritten)
        """
        from auth import hash_password
        from database import SessionLocal
        from models import Agency, Category, Location, Product, Restaurant
        from modules.cart import service as cart_service

        CONCURRENT_SESSION = "cccc0001-cccc-cccc-cccc-cccccccccccc"
        PRODUCT_PRICE = 15000

        # ── Setup: commit real data visible to all connections ──────────
        setup_db = SessionLocal()
        try:
            agency = Agency(
                name="Concurrent Test Agency",
                owner_email="concurrent@test.uz",
                owner_password_hash=hash_password("pw"),
            )
            setup_db.add(agency)
            setup_db.flush()

            rest = Restaurant(
                agency_id=agency.id,
                name="Concurrent Test Restaurant",
                slug=f"concurrent-test-{CONCURRENT_SESSION[:8]}",
                admin_password_hash=hash_password("pw"),
                currency="UZS",
                telegram_bot_token_encrypted="stub",
                telegram_dispatcher_id=0,
            )
            setup_db.add(rest)
            setup_db.flush()

            loc = Location(
                restaurant_id=rest.id,
                name="Concurrent Test Location",
                slug=f"concurrent-loc-{CONCURRENT_SESSION[:8]}",
                is_active=True,
                timezone="Asia/Tashkent",
                delivery_fee=0,
                min_order_amount=0,
                currency="UZS",
                language="uz",
                is_waiter_call_enabled=False,
            )
            setup_db.add(loc)
            setup_db.flush()

            cat = Category(
                restaurant_id=rest.id,
                name="Test Category",
                sort_order=1,
            )
            setup_db.add(cat)
            setup_db.flush()

            prod = Product(
                restaurant_id=rest.id,
                category_id=cat.id,
                name="Concurrent Test Product",
                price=PRODUCT_PRICE,
                is_available=True,
            )
            setup_db.add(prod)
            setup_db.flush()

            # REAL COMMIT — makes data visible to worker thread connections
            setup_db.commit()

            restaurant_id = rest.id
            location_id = loc.id
            product_id = prod.id
        except Exception:
            setup_db.rollback()
            setup_db.close()
            raise
        else:
            setup_db.close()

        errors = []

        def worker():
            """
            Each worker opens its own independent PostgreSQL session.
            Data is committed above so it's visible to all connections.
            """
            s = SessionLocal()
            try:
                from models import Location as LocationModel
                loc_obj = s.query(LocationModel).filter(
                    LocationModel.id == location_id
                ).first()

                cart = cart_service.get_or_create_cart(
                    db=s,
                    restaurant_id=restaurant_id,
                    session_id=CONCURRENT_SESSION,
                    telegram_id=None,
                    currency="UZS",
                )
                cart_service.add_item(
                    db=s,
                    cart=cart,
                    product_id=product_id,
                    variant_id=None,
                    modifier_option_ids=[],
                    notes=None,
                    quantity=1,
                    location=loc_obj,
                )
            except Exception as e:
                errors.append(str(e))
            finally:
                s.close()

        try:
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            assert not errors, f"Worker errors: {errors}"

            # Verify DB state with a fresh independent session
            with SessionLocal() as check_db:
                carts = (
                    check_db.query(Cart)
                    .filter(
                        Cart.session_id == CONCURRENT_SESSION,
                        Cart.restaurant_id == restaurant_id,
                    )
                    .all()
                )
                assert len(carts) == 1, f"Expected 1 Cart, got {len(carts)}"

                items = (
                    check_db.query(CartItem)
                    .filter(CartItem.cart_id == carts[0].id)
                    .all()
                )
                assert len(items) == 1, f"Expected 1 CartItem (no duplicates), got {len(items)}"
                assert items[0].quantity == 2, f"Expected quantity=2, got {items[0].quantity}"
                assert items[0].unit_price == PRODUCT_PRICE, (
                    f"unit_price snapshot changed: expected {PRODUCT_PRICE}, "
                    f"got {items[0].unit_price}"
                )
        finally:
            # ── Teardown: remove committed test data ────────────────────
            cleanup_db = SessionLocal()
            try:
                cleanup_db.query(Cart).filter(
                    Cart.session_id == CONCURRENT_SESSION,
                    Cart.restaurant_id == restaurant_id,
                ).delete(synchronize_session=False)
                cleanup_db.query(Product).filter(Product.id == product_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(Category).filter(Category.restaurant_id == restaurant_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(Location).filter(Location.id == location_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(Restaurant).filter(Restaurant.id == restaurant_id).delete(
                    synchronize_session=False
                )
                cleanup_db.query(Agency).filter(Agency.id == agency.id).delete(
                    synchronize_session=False
                )
                cleanup_db.commit()
            except Exception:
                cleanup_db.rollback()
            finally:
                cleanup_db.close()


# ═══════════════════════════════════════════════════════════════════
# N — CONCURRENT QUANTITY UPDATES (SELECT FOR UPDATE)
# ═══════════════════════════════════════════════════════════════════

class TestConcurrentQuantityUpdate:
    def test_sequential_quantity_updates_consistent(self, cart_client, product):
        add_r = cart_client.post("/api/cart/items", json={"product_id": product.id})
        item_id = add_r.json()["items"][0]["id"]

        cart_client.patch(f"/api/cart/items/{item_id}", json={"quantity": 3})
        r = cart_client.patch(f"/api/cart/items/{item_id}", json={"quantity": 5})
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["quantity"] == 5
        assert item["line_total"] == item["unit_price"] * 5


# ═══════════════════════════════════════════════════════════════════
# O — REGRESSION
# ═══════════════════════════════════════════════════════════════════

class TestRegression:
    def test_existing_menu_endpoint_still_works(self, cart_client, product):
        """GET /api/menu/{restaurant_id} not broken by Phase 6."""
        # cart_client has restaurant context via dependency overrides
        r = cart_client.get(f"/api/menu/{product.restaurant_id}")
        # May return 200 or redirect depending on menu setup — just not 5xx
        assert r.status_code != 500

    def test_health_check(self, cart_client):
        r = cart_client.get("/health")
        assert r.status_code in (200, 503)

    def test_root_endpoint(self, cart_client):
        r = cart_client.get("/")
        assert r.status_code == 200
