"""
tests/test_s2_3_variants.py — S2-3: ProductVariant CRUD API

Покрытие:
  - CRUD (create / list / update / delete)
  - Validation (price=0, price<0, empty name)
  - Tenant isolation / IDOR (foreign product/variant → 404)
  - Sorting (sort_order ASC, id ASC tie-breaker)
  - inactive variant lifecycle
  - cascade delete (delete product → variants cascade)

Baseline: 659 passed, 21 failed, 1 skipped (после S2-2).
"""

import pytest
from fastapi.testclient import TestClient

from api import app
from auth import get_current_restaurant_admin, get_telegram_user
from database import get_db
from models import Product, ProductVariant


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def _client_as(db, restaurant, tg_user):
    """TestClient авторизованный под restaurant."""
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant
    app.dependency_overrides[get_telegram_user] = lambda: tg_user
    c = TestClient(app, raise_server_exceptions=True)
    return c


# ──────────────────────────────────────────
# Local fixtures
# ──────────────────────────────────────────

@pytest.fixture
def category2(db, restaurant2):
    from models import Category
    c = Category(restaurant_id=restaurant2.id, name="Десерты", sort_order=1)
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def product_r2(db, restaurant2, category2):
    p = Product(
        restaurant_id=restaurant2.id,
        category_id=category2.id,
        name="Тирамису",
        price=20000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def variant(db, product):
    """Вариант продукта основного ресторана."""
    v = ProductVariant(
        product_id=product.id,
        name="Полная порция",
        price=15000,
        sort_order=0,
        is_active=True,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def variant_r2(db, product_r2):
    """Вариант продукта второго ресторана."""
    v = ProductVariant(
        product_id=product_r2.id,
        name="Маленький",
        price=10000,
        sort_order=0,
        is_active=True,
    )
    db.add(v)
    db.flush()
    return v


# ──────────────────────────────────────────
# CRUD — Create
# ──────────────────────────────────────────

def test_create_variant_201(db, restaurant, product, tg_user):
    """Создание варианта своего продукта → 201."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product.id}/variants/",
            json={"name": "Полная порция", "price": 35000, "sort_order": 0},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Полная порция"
        assert body["price"] == 35000
        assert body["product_id"] == product.id
        assert body["is_active"] is True
        assert "id" in body
    finally:
        app.dependency_overrides.clear()


def test_create_variant_price_zero_201(db, restaurant, product, tg_user):
    """price=0 допускается (бесплатный вариант)."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product.id}/variants/",
            json={"name": "Бесплатно", "price": 0},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["price"] == 0
    finally:
        app.dependency_overrides.clear()


def test_create_variant_inactive_201(db, restaurant, product, tg_user):
    """Создание неактивного варианта → 201."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product.id}/variants/",
            json={"name": "Неактивный", "price": 5000, "is_active": False},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_active"] is False
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────
# CRUD — List
# ──────────────────────────────────────────

def test_list_variants_200(db, restaurant, product, variant, tg_user):
    """Список вариантов своего продукта → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/product/{product.id}/variants")
        assert resp.status_code == 200, resp.text
        ids = [v["id"] for v in resp.json()]
        assert variant.id in ids
    finally:
        app.dependency_overrides.clear()


def test_variants_sorted_by_sort_order_then_id(db, restaurant, product, tg_user):
    """Варианты сортируются: sort_order ASC, id ASC (tie-breaker)."""
    v1 = ProductVariant(product_id=product.id, name="B", price=10000, sort_order=2, is_active=True)
    v2 = ProductVariant(product_id=product.id, name="A", price=10000, sort_order=1, is_active=True)
    v3 = ProductVariant(product_id=product.id, name="C", price=10000, sort_order=1, is_active=True)
    db.add_all([v1, v2, v3])
    db.flush()

    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/product/{product.id}/variants")
        assert resp.status_code == 200, resp.text
        names = [v["name"] for v in resp.json()]
        # sort_order=1 → v2, v3 (by id asc); sort_order=2 → v1
        assert names.index("A") < names.index("C"), "sort_order=1: A(меньший id) перед C"
        assert names.index("C") < names.index("B"), "sort_order=1 перед sort_order=2"
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────
# CRUD — Update
# ──────────────────────────────────────────

def test_update_variant_200(db, restaurant, variant, tg_user):
    """Обновление варианта своего продукта → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/menu/variant/{variant.id}",
            json={"name": "Половина порции", "price": 8000},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Половина порции"
        assert body["price"] == 8000
    finally:
        app.dependency_overrides.clear()


def test_update_variant_inactive(db, restaurant, variant, tg_user):
    """Деактивация варианта → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(f"/api/menu/variant/{variant.id}", json={"is_active": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────
# CRUD — Delete
# ──────────────────────────────────────────

def test_delete_variant_204(db, restaurant, variant, tg_user):
    """Удаление варианта → 204."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/variant/{variant.id}")
        assert resp.status_code == 204, resp.text
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────
# Validation
# ──────────────────────────────────────────

def test_create_variant_negative_price_422(db, restaurant, product, tg_user):
    """price < 0 → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product.id}/variants/",
            json={"name": "Плохой", "price": -1},
        )
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_variant_empty_name_422(db, restaurant, product, tg_user):
    """Пустое name → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product.id}/variants/",
            json={"name": "", "price": 1000},
        )
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_update_variant_negative_price_422(db, restaurant, variant, tg_user):
    """PATCH: price < 0 → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(f"/api/menu/variant/{variant.id}", json={"price": -100})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_update_variant_empty_name_422(db, restaurant, variant, tg_user):
    """PATCH: пустое name → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(f"/api/menu/variant/{variant.id}", json={"name": ""})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────
# Tenant Isolation / IDOR
# ──────────────────────────────────────────

@pytest.mark.security
def test_idor_variant_create_foreign_product(
    db, restaurant, product_r2, tg_user
):
    """Restaurant A не может создать вариант для продукта Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product_r2.id}/variants/",
            json={"name": "Взломанный", "price": 1000},
        )
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_variant_list_foreign_product(
    db, restaurant, product_r2, tg_user
):
    """Restaurant A не может получить варианты продукта Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/product/{product_r2.id}/variants")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_variant_patch_foreign(
    db, restaurant, variant_r2, tg_user
):
    """Restaurant A не может изменить вариант продукта Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/menu/variant/{variant_r2.id}",
            json={"name": "Взломано"},
        )
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_variant_delete_foreign(
    db, restaurant, variant_r2, tg_user
):
    """Restaurant A не может удалить вариант продукта Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/variant/{variant_r2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────
# Cascade delete
# ──────────────────────────────────────────

def test_delete_product_cascades_variants(db, restaurant, product, variant, tg_user):
    """
    Удаление продукта → варианты удаляются каскадно (DB constraint + ORM).
    """
    variant_id = variant.id
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/product/{product.id}")
        assert resp.status_code == 204, resp.text
    finally:
        app.dependency_overrides.clear()

    remaining = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    assert remaining is None, "Вариант должен быть удалён каскадно"
