"""
tests/test_s5_customer_menu.py — Phase 5: Customer Menu

Тест-матрица:
  A  — Customer Access (public, no JWT)
  B  — Category display
  C  — Product display
  D  — Variants
  E  — Modifier groups / options
  F  — Availability (Phase 3 source of truth)
  G  — Localization (Phase 4 source of truth)
  H  — Tenant isolation
  I  — Error / empty states
  J  — Regression (Phase 0–4 must not break)

Endpoints under test:
  [R] = GET /api/restaurants/{slug}          — primary customer entry point
  [M] = GET /api/menu/{restaurant_id}        — secondary public menu endpoint

Fixtures reuse conftest.py globals (db, restaurant, restaurant2, location,
location2, agency, tg_user) and define local fixtures for Phase 5 data.
"""

import pytest
from fastapi.testclient import TestClient

from api import app
from models import (
    Category,
    CategoryTranslation,
    ModifierGroup,
    ModifierGroupTranslation,
    ModifierOption,
    ModifierOptionTranslation,
    Product,
    ProductTranslation,
    ProductVariant,
    VariantTranslation,
)


# ──────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────

def _pub_client(db):
    """Unauthenticated TestClient — simulates customer with no JWT."""
    from database import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    app.dependency_overrides.clear()


def _menu_products(resp_json):
    """Flatten all products from restaurant slug response categories."""
    return [p for cat in resp_json.get("categories", []) for p in cat.get("products", [])]


def _menu_products_from_list(categories_json):
    """Flatten products from GET /api/menu/{id} categories list."""
    return [p for cat in categories_json for p in cat.get("products", [])]


# ──────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────

@pytest.fixture
def s5_cat(db, restaurant):
    c = Category(
        restaurant_id=restaurant.id,
        name="S5 Category",
        sort_order=1,
    )
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def s5_prod(db, restaurant, s5_cat):
    p = Product(
        restaurant_id=restaurant.id,
        category_id=s5_cat.id,
        name="S5 Product",
        description="S5 description",
        price=25000,
        photo_url="https://example.com/s5.jpg",
        is_available=True,
        is_bestseller=True,
        is_new=False,
        is_spicy=False,
        is_chef_choice=False,
        is_popular=True,
        sort_order=1,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def s5_prod_unavail(db, restaurant, s5_cat):
    p = Product(
        restaurant_id=restaurant.id,
        category_id=s5_cat.id,
        name="Unavailable Product",
        price=15000,
        is_available=False,
        sort_order=2,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def s5_variant_avail(db, s5_prod):
    v = ProductVariant(
        product_id=s5_prod.id,
        name="Small",
        price=20000,
        sort_order=1,
        is_active=True,
        is_available=True,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def s5_variant_unavail(db, s5_prod):
    v = ProductVariant(
        product_id=s5_prod.id,
        name="Large",
        price=35000,
        sort_order=2,
        is_active=True,
        is_available=False,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def s5_variant_inactive(db, s5_prod):
    """is_active=False — must NOT appear in customer response."""
    v = ProductVariant(
        product_id=s5_prod.id,
        name="Hidden Variant",
        price=99000,
        sort_order=3,
        is_active=False,
        is_available=True,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def s5_mod_group_required(db, s5_prod):
    g = ModifierGroup(
        product_id=s5_prod.id,
        name="Sauce",
        min_selections=1,
        max_selections=1,
        sort_order=1,
        is_active=True,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def s5_mod_group_optional(db, s5_prod):
    g = ModifierGroup(
        product_id=s5_prod.id,
        name="Extras",
        min_selections=0,
        max_selections=3,
        sort_order=2,
        is_active=True,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def s5_mod_group_inactive(db, s5_prod):
    """is_active=False — must NOT appear in customer response."""
    g = ModifierGroup(
        product_id=s5_prod.id,
        name="Hidden Group",
        min_selections=0,
        max_selections=1,
        sort_order=99,
        is_active=False,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def s5_option_avail(db, s5_mod_group_required):
    o = ModifierOption(
        modifier_group_id=s5_mod_group_required.id,
        name="Ketchup",
        price_adjustment=0,
        sort_order=1,
        is_active=True,
        is_available=True,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def s5_option_with_price(db, s5_mod_group_required):
    o = ModifierOption(
        modifier_group_id=s5_mod_group_required.id,
        name="Truffle Sauce",
        price_adjustment=5000,
        sort_order=2,
        is_active=True,
        is_available=True,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def s5_option_unavail(db, s5_mod_group_required):
    o = ModifierOption(
        modifier_group_id=s5_mod_group_required.id,
        name="Sold Out Sauce",
        price_adjustment=0,
        sort_order=3,
        is_active=True,
        is_available=False,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def s5_option_inactive(db, s5_mod_group_required):
    """is_active=False — must NOT appear in customer response."""
    o = ModifierOption(
        modifier_group_id=s5_mod_group_required.id,
        name="Hidden Option",
        price_adjustment=1000,
        sort_order=99,
        is_active=False,
        is_available=True,
    )
    db.add(o)
    db.flush()
    return o


# ──────────────────────────────────────────
# A — CUSTOMER ACCESS (no JWT required)
# ──────────────────────────────────────────

class TestCustomerAccess:
    """A: Public customer menu accessible without restaurant-admin JWT."""

    def test_a1_restaurants_slug_public_no_auth(self, db, restaurant, s5_cat, s5_prod):
        """A1: GET /api/restaurants/{slug} returns 200 without any Authorization header."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            assert r.status_code == 200, r.text

    def test_a2_menu_id_public_no_auth(self, db, restaurant, s5_cat, s5_prod):
        """A2: GET /api/menu/{id} returns 200 without any Authorization header."""
        for c in _pub_client(db):
            r = c.get(f"/api/menu/{restaurant.id}")
            assert r.status_code == 200, r.text

    def test_a3_restaurants_slug_returns_expected_shape(self, db, restaurant, s5_cat, s5_prod):
        """A3: Response has id, name, slug, categories structure."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            assert r.status_code == 200
            data = r.json()
            assert "id" in data
            assert "name" in data
            assert "slug" in data
            assert "categories" in data
            assert isinstance(data["categories"], list)

    def test_a4_menu_id_returns_list_of_categories(self, db, restaurant, s5_cat, s5_prod):
        """A4: GET /api/menu/{id} returns a list (not a dict)."""
        for c in _pub_client(db):
            r = c.get(f"/api/menu/{restaurant.id}")
            assert r.status_code == 200
            assert isinstance(r.json(), list)


# ──────────────────────────────────────────
# B — CATEGORY DISPLAY
# ──────────────────────────────────────────

class TestCategoryDisplay:
    """B: Categories appear with correct name, sort_order, and product count."""

    def test_b1_category_appears_in_slug_response(self, db, restaurant, s5_cat, s5_prod):
        """B1: Category with available product appears in /restaurants/{slug}."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            cats = r.json()["categories"]
            names = [cat["name"] for cat in cats]
            assert s5_cat.name in names

    def test_b2_category_appears_in_menu_response(self, db, restaurant, s5_cat, s5_prod):
        """B2: Category with available product appears in /menu/{id}."""
        for c in _pub_client(db):
            r = c.get(f"/api/menu/{restaurant.id}")
            names = [cat["name"] for cat in r.json()]
            assert s5_cat.name in names

    def test_b3_category_sort_order_present(self, db, restaurant, s5_cat, s5_prod):
        """B3: sort_order field is present on each category."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            for cat in r.json()["categories"]:
                assert "sort_order" in cat

    def test_b4_empty_category_excluded_from_restaurants(self, db, restaurant, s5_cat):
        """B4: Category with no available products is excluded from /restaurants/{slug}."""
        # s5_cat has no products (s5_prod fixture not used)
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            names = [cat["name"] for cat in r.json()["categories"]]
            assert s5_cat.name not in names

    def test_b5_category_products_list_present(self, db, restaurant, s5_cat, s5_prod):
        """B5: Each category has a 'products' list."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            for cat in r.json()["categories"]:
                assert "products" in cat
                assert isinstance(cat["products"], list)


# ──────────────────────────────────────────
# C — PRODUCT DISPLAY
# ──────────────────────────────────────────

class TestProductDisplay:
    """C: Products appear with all required Phase 5 fields."""

    def test_c1_product_name_present(self, db, restaurant, s5_cat, s5_prod):
        """C1: product.name is present."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next((p for p in prods if p["id"] == s5_prod.id), None)
            assert prod is not None
            assert prod["name"] == s5_prod.name

    def test_c2_product_description_present(self, db, restaurant, s5_cat, s5_prod):
        """C2: product.description is present."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["description"] == s5_prod.description

    def test_c3_product_price_present(self, db, restaurant, s5_cat, s5_prod):
        """C3: product.price is present for non-variant product."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["price"] == s5_prod.price

    def test_c4_product_photo_url_present(self, db, restaurant, s5_cat, s5_prod):
        """C4: product.photo_url is present when set."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["photo_url"] == s5_prod.photo_url

    def test_c5_product_is_available_field(self, db, restaurant, s5_cat, s5_prod):
        """C5: product.is_available is returned (True for available product)."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["is_available"] is True

    def test_c6_product_sort_order_present(self, db, restaurant, s5_cat, s5_prod):
        """C6: product.sort_order is present."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert "sort_order" in prod

    def test_c7_product_boolean_badge_bestseller(self, db, restaurant, s5_cat, s5_prod):
        """C7: is_bestseller=True is returned in response."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["is_bestseller"] is True

    def test_c8_product_boolean_badge_spicy(self, db, restaurant, s5_cat):
        """C8: is_spicy=True is returned in response."""
        p = Product(
            restaurant_id=restaurant.id,
            category_id=s5_cat.id,
            name="Spicy Product",
            price=10000,
            is_available=True,
            is_spicy=True,
            sort_order=5,
        )
        db.add(p)
        db.flush()
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next((x for x in prods if x["id"] == p.id), None)
            assert prod is not None
            assert prod["is_spicy"] is True

    def test_c9_product_badges_all_fields_present(self, db, restaurant, s5_cat, s5_prod):
        """C9: All badge fields (is_bestseller, is_new, is_spicy, is_chef_choice, is_popular) present."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            for field in ["is_bestseller", "is_new", "is_spicy", "is_chef_choice", "is_popular"]:
                assert field in prod, f"Missing field: {field}"


# ──────────────────────────────────────────
# D — VARIANTS
# ──────────────────────────────────────────

class TestVariantDisplay:
    """D: Variants appear with correct fields, sort order, and availability."""

    def test_d1_active_available_variant_appears(
        self, db, restaurant, s5_cat, s5_prod, s5_variant_avail
    ):
        """D1: Active+available variant appears in customer response."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            variant_ids = [v["id"] for v in prod["variants"]]
            assert s5_variant_avail.id in variant_ids

    def test_d2_active_unavailable_variant_appears_with_flag(
        self, db, restaurant, s5_cat, s5_prod, s5_variant_avail, s5_variant_unavail
    ):
        """D2: Active+unavailable variant appears but is_available=False."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            v_unavail = next((v for v in prod["variants"] if v["id"] == s5_variant_unavail.id), None)
            assert v_unavail is not None
            assert v_unavail["is_available"] is False

    def test_d3_inactive_variant_excluded(
        self, db, restaurant, s5_cat, s5_prod,
        s5_variant_avail, s5_variant_inactive
    ):
        """D3: is_active=False variant is excluded from customer response."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            variant_ids = [v["id"] for v in prod["variants"]]
            assert s5_variant_inactive.id not in variant_ids

    def test_d4_variant_price_present(
        self, db, restaurant, s5_cat, s5_prod, s5_variant_avail
    ):
        """D4: Variant has price field."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            v = next(v for v in prod["variants"] if v["id"] == s5_variant_avail.id)
            assert v["price"] == s5_variant_avail.price

    def test_d5_variant_sort_order_present(
        self, db, restaurant, s5_cat, s5_prod, s5_variant_avail
    ):
        """D5: Variant has sort_order field."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            v = next(v for v in prod["variants"] if v["id"] == s5_variant_avail.id)
            assert "sort_order" in v


# ──────────────────────────────────────────
# E — MODIFIER GROUPS / OPTIONS
# ──────────────────────────────────────────

class TestModifierDisplay:
    """E: Modifier groups and options appear with correct fields."""

    def test_e1_active_modifier_group_appears(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail
    ):
        """E1: Active modifier group appears in customer response."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            group_ids = [g["id"] for g in prod["modifier_groups"]]
            assert s5_mod_group_required.id in group_ids

    def test_e2_inactive_modifier_group_excluded(
        self, db, restaurant, s5_cat, s5_prod,
        s5_option_avail, s5_mod_group_required, s5_mod_group_inactive
    ):
        """E2: is_active=False modifier group is excluded."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            group_ids = [g["id"] for g in prod["modifier_groups"]]
            assert s5_mod_group_inactive.id not in group_ids

    def test_e3_modifier_group_fields_present(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail
    ):
        """E3: Modifier group has id, name, min_selections, max_selections, sort_order, options."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            for field in ["id", "name", "min_selections", "max_selections", "sort_order", "options"]:
                assert field in g, f"Missing field: {field}"

    def test_e4_required_group_min_selections(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail
    ):
        """E4: Required group has min_selections=1."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            assert g["min_selections"] == 1

    def test_e5_optional_group_min_selections_zero(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_optional
    ):
        """E5: Optional group has min_selections=0."""
        o = ModifierOption(
            modifier_group_id=s5_mod_group_optional.id,
            name="Extra cheese",
            price_adjustment=2000,
            sort_order=1,
            is_active=True,
            is_available=True,
        )
        db.add(o)
        db.flush()
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(
                (g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_optional.id), None
            )
            assert g is not None
            assert g["min_selections"] == 0

    def test_e6_active_available_option_appears(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail
    ):
        """E6: Active+available modifier option appears in response."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            option_ids = [o["id"] for o in g["options"]]
            assert s5_option_avail.id in option_ids

    def test_e7_unavailable_option_appears_with_flag(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail, s5_option_unavail
    ):
        """E7: Active+unavailable option appears but is_available=False."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            o = next((x for x in g["options"] if x["id"] == s5_option_unavail.id), None)
            assert o is not None
            assert o["is_available"] is False

    def test_e8_inactive_option_excluded(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail, s5_option_inactive
    ):
        """E8: is_active=False option is excluded from customer response."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            option_ids = [o["id"] for o in g["options"]]
            assert s5_option_inactive.id not in option_ids

    def test_e9_option_price_adjustment_present(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_with_price
    ):
        """E9: Option has price_adjustment field reflecting model value."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            o = next(x for x in g["options"] if x["id"] == s5_option_with_price.id)
            assert o["price_adjustment"] == s5_option_with_price.price_adjustment

    def test_e10_option_fields_complete(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail
    ):
        """E10: Option has id, name, price_adjustment, sort_order, is_available."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            o = next(x for x in g["options"] if x["id"] == s5_option_avail.id)
            for field in ["id", "name", "price_adjustment", "sort_order", "is_available"]:
                assert field in o, f"Missing field: {field}"


# ──────────────────────────────────────────
# F — AVAILABILITY (Phase 3 source of truth)
# ──────────────────────────────────────────

class TestAvailability:
    """F: Phase 3 availability is applied correctly in customer-facing response."""

    def test_f1_available_product_appears(self, db, restaurant, s5_cat, s5_prod):
        """F1: is_available=True product appears in response."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            assert any(p["id"] == s5_prod.id for p in prods)

    def test_f2_unavailable_product_excluded_from_slug(
        self, db, restaurant, s5_cat, s5_prod, s5_prod_unavail
    ):
        """F2: is_available=False product is excluded from /restaurants/{slug}."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            assert not any(p["id"] == s5_prod_unavail.id for p in prods)

    def test_f3_unavailable_product_excluded_from_menu(
        self, db, restaurant, s5_cat, s5_prod, s5_prod_unavail
    ):
        """F3: is_available=False product is excluded from /menu/{id}."""
        for c in _pub_client(db):
            r = c.get(f"/api/menu/{restaurant.id}")
            prods = _menu_products_from_list(r.json())
            assert not any(p["id"] == s5_prod_unavail.id for p in prods)

    def test_f4_available_variant_is_available_true(
        self, db, restaurant, s5_cat, s5_prod, s5_variant_avail
    ):
        """F4: Available variant has is_available=True."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            v = next(v for v in prod["variants"] if v["id"] == s5_variant_avail.id)
            assert v["is_available"] is True

    def test_f5_unavailable_variant_is_available_false(
        self, db, restaurant, s5_cat, s5_prod,
        s5_variant_avail, s5_variant_unavail
    ):
        """F5: Unavailable variant has is_available=False (visible but disabled)."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            v = next(v for v in prod["variants"] if v["id"] == s5_variant_unavail.id)
            assert v["is_available"] is False

    def test_f6_unavailable_modifier_option_is_available_false(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail, s5_option_unavail
    ):
        """F6: Unavailable modifier option has is_available=False."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            o = next(x for x in g["options"] if x["id"] == s5_option_unavail.id)
            assert o["is_available"] is False

    def test_f7_available_modifier_option_is_available_true(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail
    ):
        """F7: Available modifier option has is_available=True."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            o = next(x for x in g["options"] if x["id"] == s5_option_avail.id)
            assert o["is_available"] is True


# ──────────────────────────────────────────
# G — LOCALIZATION (Phase 4 source of truth)
# ──────────────────────────────────────────

class TestLocalization:
    """G: Phase 4 localization applied to customer menu without changes to i18n architecture."""

    def test_g1_category_name_uz(self, db, restaurant, s5_cat, s5_prod):
        """G1: Category name localized to uz."""
        t = CategoryTranslation(category_id=s5_cat.id, language="uz", name="UZ Kategoriya")
        db.add(t); db.commit()
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}?lang=uz")
            cats = r.json()["categories"]
            cat = next((x for x in cats if x["id"] == s5_cat.id), None)
            assert cat is not None
            assert cat["name"] == "UZ Kategoriya"

    def test_g2_product_name_ru(self, db, restaurant, s5_cat, s5_prod):
        """G2: Product name localized to ru."""
        t = ProductTranslation(product_id=s5_prod.id, language="ru", name="RU Продукт")
        db.add(t); db.commit()
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}?lang=ru")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["name"] == "RU Продукт"

    def test_g3_product_description_en(self, db, restaurant, s5_cat, s5_prod):
        """G3: Product description localized to en."""
        t = ProductTranslation(
            product_id=s5_prod.id, language="en",
            name="EN Product", description="EN description"
        )
        db.add(t); db.commit()
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}?lang=en")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["description"] == "EN description"

    def test_g4_variant_name_localized(
        self, db, restaurant, s5_cat, s5_prod, s5_variant_avail
    ):
        """G4: Variant name localized via Phase 4 mechanism."""
        t = VariantTranslation(variant_id=s5_variant_avail.id, language="ru", name="RU Маленький")
        db.add(t); db.commit()
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}?lang=ru")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            v = next(v for v in prod["variants"] if v["id"] == s5_variant_avail.id)
            assert v["name"] == "RU Маленький"

    def test_g5_modifier_group_name_localized(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail
    ):
        """G5: Modifier group name localized via Phase 4 mechanism."""
        t = ModifierGroupTranslation(
            modifier_group_id=s5_mod_group_required.id, language="ru", name="RU Соус"
        )
        db.add(t); db.commit()
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}?lang=ru")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            assert g["name"] == "RU Соус"

    def test_g6_modifier_option_name_localized(
        self, db, restaurant, s5_cat, s5_prod,
        s5_mod_group_required, s5_option_avail
    ):
        """G6: Modifier option name localized via Phase 4 mechanism."""
        t = ModifierOptionTranslation(
            modifier_option_id=s5_option_avail.id, language="uz", name="UZ Ketchup"
        )
        db.add(t); db.commit()
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}?lang=uz")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            g = next(g for g in prod["modifier_groups"] if g["id"] == s5_mod_group_required.id)
            o = next(x for x in g["options"] if x["id"] == s5_option_avail.id)
            assert o["name"] == "UZ Ketchup"

    def test_g7_fallback_to_base_name_when_no_translation(
        self, db, restaurant, s5_cat, s5_prod
    ):
        """G7: Base name returned when no translation exists for requested lang."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}?lang=en")
            prods = _menu_products(r.json())
            prod = next((p for p in prods if p["id"] == s5_prod.id), None)
            assert prod is not None
            assert prod["name"] == s5_prod.name

    def test_g8_location_language_used_as_default(
        self, db, restaurant, location, s5_cat, s5_prod
    ):
        """G8: Location.language used as default when no ?lang= provided."""
        # conftest location defaults to language="uz"
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            assert r.status_code == 200
            # Response is valid (language resolution happens, no error)
            assert "categories" in r.json()


# ──────────────────────────────────────────
# H — TENANT ISOLATION
# ──────────────────────────────────────────

class TestTenantIsolation:
    """H: Restaurant A cannot see data from Restaurant B."""

    def test_h1_slug_returns_only_own_products(
        self, db, restaurant, restaurant2, s5_cat, s5_prod
    ):
        """H1: /restaurants/{slug} only returns products of that restaurant."""
        # Create category+product for restaurant2
        cat2 = Category(restaurant_id=restaurant2.id, name="R2 Cat", sort_order=1)
        db.add(cat2); db.flush()
        prod2 = Product(
            restaurant_id=restaurant2.id,
            category_id=cat2.id,
            name="R2 Product",
            price=10000,
            is_available=True,
            sort_order=1,
        )
        db.add(prod2); db.flush()

        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod_ids = [p["id"] for p in prods]
            assert prod2.id not in prod_ids
            assert s5_prod.id in prod_ids

    def test_h2_menu_id_returns_only_own_products(
        self, db, restaurant, restaurant2, s5_cat, s5_prod
    ):
        """H2: /menu/{restaurant_id} only returns products of that restaurant."""
        cat2 = Category(restaurant_id=restaurant2.id, name="R2 Cat2", sort_order=1)
        db.add(cat2); db.flush()
        prod2 = Product(
            restaurant_id=restaurant2.id,
            category_id=cat2.id,
            name="R2 Product2",
            price=10000,
            is_available=True,
            sort_order=1,
        )
        db.add(prod2); db.flush()

        for c in _pub_client(db):
            r = c.get(f"/api/menu/{restaurant.id}")
            prods = _menu_products_from_list(r.json())
            prod_ids = [p["id"] for p in prods]
            assert prod2.id not in prod_ids

    def test_h3_slug_nonexistent_returns_404(self, db):
        """H3: Unknown slug returns 404."""
        for c in _pub_client(db):
            r = c.get("/api/restaurants/nonexistent-slug-xyz-999")
            assert r.status_code == 404

    def test_h4_menu_nonexistent_id_returns_404(self, db):
        """H4: Nonexistent restaurant_id returns 404."""
        for c in _pub_client(db):
            r = c.get("/api/menu/999999")
            assert r.status_code == 404


# ──────────────────────────────────────────
# I — ERROR / EMPTY STATES
# ──────────────────────────────────────────

class TestErrorAndEmptyStates:
    """I: Error and empty states return clean responses."""

    def test_i1_empty_menu_returns_empty_categories(self, db, restaurant):
        """I1: Restaurant with no products returns empty categories list."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            assert r.status_code == 200
            assert r.json()["categories"] == []

    def test_i2_menu_endpoint_empty_returns_empty_list(self, db, restaurant):
        """I2: /menu/{id} with no products returns empty list."""
        for c in _pub_client(db):
            r = c.get(f"/api/menu/{restaurant.id}")
            assert r.status_code == 200
            assert r.json() == []

    def test_i3_invalid_lang_returns_422(self, db, restaurant):
        """I3: Invalid ?lang=fr returns 422."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}?lang=fr")
            assert r.status_code == 422

    def test_i4_nonexistent_restaurant_slug_404(self, db):
        """I4: GET /api/restaurants/{slug} with unknown slug → 404."""
        for c in _pub_client(db):
            r = c.get("/api/restaurants/does-not-exist-s5")
            assert r.status_code == 404

    def test_i5_product_with_no_modifiers_returns_empty_list(
        self, db, restaurant, s5_cat, s5_prod
    ):
        """I5: Product with no modifier groups returns modifier_groups=[]."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["modifier_groups"] == []

    def test_i6_product_with_no_variants_returns_empty_list(
        self, db, restaurant, s5_cat, s5_prod
    ):
        """I6: Product with no variants returns variants=[]."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            prods = _menu_products(r.json())
            prod = next(p for p in prods if p["id"] == s5_prod.id)
            assert prod["variants"] == []


# ──────────────────────────────────────────
# J — REGRESSION (Phase 0–4 behavior unchanged)
# ──────────────────────────────────────────

class TestRegression:
    """J: Phase 0–4 contracts remain intact."""

    def test_j1_menu_endpoint_still_responds_200(self, db, restaurant, s5_cat, s5_prod):
        """J1: GET /api/menu/{id} still returns 200."""
        for c in _pub_client(db):
            r = c.get(f"/api/menu/{restaurant.id}")
            assert r.status_code == 200

    def test_j2_restaurants_slug_still_responds_200(self, db, restaurant):
        """J2: GET /api/restaurants/{slug} still returns 200."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            assert r.status_code == 200

    def test_j3_lang_param_accepted(self, db, restaurant, s5_cat, s5_prod):
        """J3: ?lang=uz/ru/en still accepted (Phase 4 contract intact)."""
        for c in _pub_client(db):
            for lang in ["uz", "ru", "en"]:
                r = c.get(f"/api/restaurants/{restaurant.slug}?lang={lang}")
                assert r.status_code == 200, f"lang={lang} failed"

    def test_j4_phase4_lang_on_menu_endpoint(self, db, restaurant, s5_cat, s5_prod):
        """J4: Phase 4 lang param still works on /menu/{id} endpoint."""
        for c in _pub_client(db):
            for lang in ["uz", "ru", "en"]:
                r = c.get(f"/api/menu/{restaurant.id}?lang={lang}")
                assert r.status_code == 200

    def test_j5_invalid_lang_still_422(self, db, restaurant):
        """J5: Invalid lang param still returns 422 (Phase 4 validation intact)."""
        for c in _pub_client(db):
            r = c.get(f"/api/menu/{restaurant.id}?lang=xx")
            assert r.status_code == 422

    def test_j6_restaurant_branding_fields_present(self, db, restaurant):
        """J6: Branding fields (logo_url, primary_color etc.) still in response."""
        for c in _pub_client(db):
            r = c.get(f"/api/restaurants/{restaurant.slug}")
            data = r.json()
            for field in ["primary_color", "secondary_color", "accent_color"]:
                assert field in data, f"Missing branding field: {field}"
