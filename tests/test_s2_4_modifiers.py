"""
tests/test_s2_4_modifiers.py — S2-4: ModifierGroup & ModifierOption CRUD API

Покрытие:
  - CRUD (create / list / update / delete)
  - Validation (min/max combinations, price_adjustment, empty name, sort_order)
  - Tenant isolation / IDOR (foreign product/group/option → 404)
  - Sorting (sort_order ASC, id ASC tie-breaker)
  - Cascade delete (group → options, product → groups → options)
  - Auth (unauthenticated → 401)

Baseline: 676 passed, 21 failed, 1 skipped (после S2-3).
"""

import pytest
from fastapi.testclient import TestClient

from api import app
from auth import get_current_restaurant_admin, get_telegram_user
from database import get_db
from models import ModifierGroup, ModifierOption, Product


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def _client_as(db, restaurant, tg_user):
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant
    app.dependency_overrides[get_telegram_user] = lambda: tg_user
    return TestClient(app, raise_server_exceptions=True)


def _anon_client(db):
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides.pop(get_current_restaurant_admin, None)
    app.dependency_overrides.pop(get_telegram_user, None)
    return TestClient(app, raise_server_exceptions=False)


# ──────────────────────────────────────────
# Local fixtures
# ──────────────────────────────────────────

@pytest.fixture
def category2(db, restaurant2):
    from models import Category
    c = Category(restaurant_id=restaurant2.id, name="Напитки", sort_order=1)
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def product_r2(db, restaurant2, category2):
    p = Product(
        restaurant_id=restaurant2.id,
        category_id=category2.id,
        name="Кола",
        price=8000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def group(db, product):
    """ModifierGroup для продукта основного ресторана."""
    g = ModifierGroup(
        product_id=product.id,
        name="Дополнительно",
        min_selections=0,
        max_selections=3,
        sort_order=0,
        is_active=True,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def group_r2(db, product_r2):
    """ModifierGroup для продукта второго ресторана."""
    g = ModifierGroup(
        product_id=product_r2.id,
        name="Лёд",
        min_selections=0,
        max_selections=1,
        sort_order=0,
        is_active=True,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def option(db, group):
    """ModifierOption для группы основного ресторана."""
    o = ModifierOption(
        modifier_group_id=group.id,
        name="Сыр",
        price_adjustment=5000,
        sort_order=0,
        is_active=True,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def option_r2(db, group_r2):
    """ModifierOption для группы второго ресторана."""
    o = ModifierOption(
        modifier_group_id=group_r2.id,
        name="Лёд",
        price_adjustment=0,
        sort_order=0,
        is_active=True,
    )
    db.add(o)
    db.flush()
    return o


# ══════════════════════════════════════════════════════════════════════════════
# MODIFIER GROUP CRUD
# ══════════════════════════════════════════════════════════════════════════════

# ── Create ─────────────────────────────────

def test_create_modifier_group_201(db, restaurant, product, tg_user):
    """Создание группы → 201, поля корректны."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product.id}/modifier-groups/",
            json={"name": "Соусы", "min_selections": 0, "max_selections": 2},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Соусы"
        assert body["min_selections"] == 0
        assert body["max_selections"] == 2
        assert body["product_id"] == product.id
        assert body["is_active"] is True
        assert "id" in body
    finally:
        app.dependency_overrides.clear()


def test_create_modifier_group_defaults(db, restaurant, product, tg_user):
    """Создание с минимальными полями — дефолты применяются."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product.id}/modifier-groups/",
            json={"name": "Топпинги"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["min_selections"] == 0
        assert body["max_selections"] == 1
        assert body["sort_order"] == 0
    finally:
        app.dependency_overrides.clear()


def test_create_modifier_group_required_201(db, restaurant, product, tg_user):
    """min_selections=1 → группа обязательная, должно создаться."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/product/{product.id}/modifier-groups/",
            json={"name": "Размер", "min_selections": 1, "max_selections": 1},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["min_selections"] == 1
    finally:
        app.dependency_overrides.clear()


# ── List ───────────────────────────────────

def test_list_modifier_groups_200(db, restaurant, product, group, tg_user):
    """Список групп своего продукта → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/product/{product.id}/modifier-groups")
        assert resp.status_code == 200, resp.text
        ids = [g["id"] for g in resp.json()]
        assert group.id in ids
    finally:
        app.dependency_overrides.clear()


def test_modifier_groups_sorted(db, restaurant, product, tg_user):
    """Сортировка: sort_order ASC, id ASC (tie-breaker)."""
    g1 = ModifierGroup(product_id=product.id, name="C", min_selections=0, max_selections=1, sort_order=2)
    g2 = ModifierGroup(product_id=product.id, name="A", min_selections=0, max_selections=1, sort_order=1)
    g3 = ModifierGroup(product_id=product.id, name="B", min_selections=0, max_selections=1, sort_order=1)
    db.add_all([g1, g2, g3])
    db.flush()

    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/product/{product.id}/modifier-groups")
        assert resp.status_code == 200, resp.text
        names = [g["name"] for g in resp.json()]
        assert names.index("A") < names.index("B"), "sort_order=1: A(меньший id) перед B"
        assert names.index("B") < names.index("C"), "sort_order=1 перед sort_order=2"
    finally:
        app.dependency_overrides.clear()


# ── Update ─────────────────────────────────

def test_update_modifier_group_200(db, restaurant, group, tg_user):
    """Обновление группы → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/menu/modifier-group/{group.id}",
            json={"name": "Начинки", "max_selections": 5},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Начинки"
        assert body["max_selections"] == 5
    finally:
        app.dependency_overrides.clear()


def test_update_modifier_group_deactivate(db, restaurant, group, tg_user):
    """Деактивация группы → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(f"/api/menu/modifier-group/{group.id}", json={"is_active": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False
    finally:
        app.dependency_overrides.clear()


# ── Delete ─────────────────────────────────

def test_delete_modifier_group_204(db, restaurant, group, tg_user):
    """Удаление группы → 204."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/modifier-group/{group.id}")
        assert resp.status_code == 204, resp.text
    finally:
        app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# MODIFIER GROUP VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def test_create_group_min0_max1_pass(db, restaurant, product, tg_user):
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/product/{product.id}/modifier-groups/",
                      json={"name": "G", "min_selections": 0, "max_selections": 1})
        assert resp.status_code == 201, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_group_min1_max3_pass(db, restaurant, product, tg_user):
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/product/{product.id}/modifier-groups/",
                      json={"name": "G", "min_selections": 1, "max_selections": 3})
        assert resp.status_code == 201, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_group_min2_max1_422(db, restaurant, product, tg_user):
    """min > max → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/product/{product.id}/modifier-groups/",
                      json={"name": "Bad", "min_selections": 2, "max_selections": 1})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_group_min_negative_422(db, restaurant, product, tg_user):
    """min_selections < 0 → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/product/{product.id}/modifier-groups/",
                      json={"name": "Bad", "min_selections": -1, "max_selections": 1})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_group_max_zero_422(db, restaurant, product, tg_user):
    """max_selections = 0 → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/product/{product.id}/modifier-groups/",
                      json={"name": "Bad", "min_selections": 0, "max_selections": 0})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_group_empty_name_422(db, restaurant, product, tg_user):
    """Пустое name → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/product/{product.id}/modifier-groups/",
                      json={"name": ""})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_patch_group_creates_invalid_min_gt_max_422(db, restaurant, group, tg_user):
    """
    Существующий max=3. PATCH max=1 при min=0 → OK.
    Существующий min=0. PATCH min=5 при max=3 → 422.
    """
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(f"/api/menu/modifier-group/{group.id}", json={"min_selections": 5})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_patch_group_both_valid_200(db, restaurant, group, tg_user):
    """PATCH min=2, max=4 одновременно → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(f"/api/menu/modifier-group/{group.id}",
                       json={"min_selections": 2, "max_selections": 4})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["min_selections"] == 2
        assert body["max_selections"] == 4
    finally:
        app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# MODIFIER OPTION CRUD
# ══════════════════════════════════════════════════════════════════════════════

# ── Create ─────────────────────────────────

def test_create_modifier_option_201(db, restaurant, group, tg_user):
    """Создание опции → 201."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(
            f"/api/menu/modifier-group/{group.id}/options/",
            json={"name": "Бекон", "price_adjustment": 3000},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Бекон"
        assert body["price_adjustment"] == 3000
        assert body["modifier_group_id"] == group.id
        assert body["is_active"] is True
    finally:
        app.dependency_overrides.clear()


def test_create_option_price_zero(db, restaurant, group, tg_user):
    """price_adjustment=0 → PASS."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/modifier-group/{group.id}/options/",
                      json={"name": "Без изменений", "price_adjustment": 0})
        assert resp.status_code == 201, resp.text
        assert resp.json()["price_adjustment"] == 0
    finally:
        app.dependency_overrides.clear()


def test_create_option_price_positive(db, restaurant, group, tg_user):
    """price_adjustment=5000 → PASS."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/modifier-group/{group.id}/options/",
                      json={"name": "Доп", "price_adjustment": 5000})
        assert resp.status_code == 201, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_option_price_negative(db, restaurant, group, tg_user):
    """price_adjustment=-5000 → PASS (скидка)."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/modifier-group/{group.id}/options/",
                      json={"name": "Скидка", "price_adjustment": -5000})
        assert resp.status_code == 201, resp.text
        assert resp.json()["price_adjustment"] == -5000
    finally:
        app.dependency_overrides.clear()


# ── List ───────────────────────────────────

def test_list_modifier_options_200(db, restaurant, group, option, tg_user):
    """Список опций → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/modifier-group/{group.id}/options")
        assert resp.status_code == 200, resp.text
        ids = [o["id"] for o in resp.json()]
        assert option.id in ids
    finally:
        app.dependency_overrides.clear()


def test_modifier_options_sorted(db, restaurant, group, tg_user):
    """Сортировка опций: sort_order ASC, id ASC (tie-breaker)."""
    o1 = ModifierOption(modifier_group_id=group.id, name="C", price_adjustment=0, sort_order=2)
    o2 = ModifierOption(modifier_group_id=group.id, name="A", price_adjustment=0, sort_order=1)
    o3 = ModifierOption(modifier_group_id=group.id, name="B", price_adjustment=0, sort_order=1)
    db.add_all([o1, o2, o3])
    db.flush()

    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/modifier-group/{group.id}/options")
        assert resp.status_code == 200, resp.text
        names = [o["name"] for o in resp.json()]
        assert names.index("A") < names.index("B")
        assert names.index("B") < names.index("C")
    finally:
        app.dependency_overrides.clear()


# ── Update ─────────────────────────────────

def test_update_modifier_option_200(db, restaurant, option, tg_user):
    """Обновление опции → 200."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/menu/modifier-option/{option.id}",
            json={"name": "Моцарелла", "price_adjustment": 7000},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Моцарелла"
        assert body["price_adjustment"] == 7000
    finally:
        app.dependency_overrides.clear()


# ── Delete ─────────────────────────────────

def test_delete_modifier_option_204(db, restaurant, option, tg_user):
    """Удаление опции → 204."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/modifier-option/{option.id}")
        assert resp.status_code == 204, resp.text
    finally:
        app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# MODIFIER OPTION VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def test_create_option_price_too_low_422(db, restaurant, group, tg_user):
    """price_adjustment < -1000000 → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/modifier-group/{group.id}/options/",
                      json={"name": "Bad", "price_adjustment": -1000001})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_option_empty_name_422(db, restaurant, group, tg_user):
    """name='' → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/modifier-group/{group.id}/options/",
                      json={"name": ""})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


def test_create_option_negative_sort_order_422(db, restaurant, group, tg_user):
    """sort_order < 0 → 422."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/modifier-group/{group.id}/options/",
                      json={"name": "Bad", "sort_order": -1})
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# TENANT ISOLATION / IDOR
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.security
def test_idor_modifier_group_create_foreign_product(db, restaurant, product_r2, tg_user):
    """Restaurant A не может создать группу для продукта Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/product/{product_r2.id}/modifier-groups/",
                      json={"name": "Взлом"})
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_modifier_group_list_foreign_product(db, restaurant, product_r2, tg_user):
    """Restaurant A не может получить группы продукта Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/product/{product_r2.id}/modifier-groups")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_modifier_group_patch_foreign(db, restaurant, group_r2, tg_user):
    """Restaurant A не может изменить группу Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(f"/api/menu/modifier-group/{group_r2.id}", json={"name": "Взлом"})
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_modifier_group_delete_foreign(db, restaurant, group_r2, tg_user):
    """Restaurant A не может удалить группу Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/modifier-group/{group_r2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_modifier_option_create_foreign_group(db, restaurant, group_r2, tg_user):
    """Restaurant A не может создать опцию для группы Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.post(f"/api/menu/modifier-group/{group_r2.id}/options/",
                      json={"name": "Взлом"})
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_modifier_option_list_foreign_group(db, restaurant, group_r2, tg_user):
    """Restaurant A не может получить опции группы Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/modifier-group/{group_r2.id}/options")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_modifier_option_patch_foreign(db, restaurant, option_r2, tg_user):
    """Restaurant A не может изменить опцию Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.patch(f"/api/menu/modifier-option/{option_r2.id}", json={"name": "Взлом"})
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_modifier_option_delete_foreign(db, restaurant, option_r2, tg_user):
    """Restaurant A не может удалить опцию Restaurant B → 404."""
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/modifier-option/{option_r2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# CASCADE DELETE
# ══════════════════════════════════════════════════════════════════════════════

def test_delete_group_cascades_options(db, restaurant, group, option, tg_user):
    """Удаление ModifierGroup → все его ModifierOption удаляются."""
    option_id = option.id
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/modifier-group/{group.id}")
        assert resp.status_code == 204, resp.text
    finally:
        app.dependency_overrides.clear()

    remaining = db.query(ModifierOption).filter(ModifierOption.id == option_id).first()
    assert remaining is None, "ModifierOption должна быть удалена каскадно"


def test_delete_product_cascades_groups_and_options(db, restaurant, product, group, option, tg_user):
    """Удаление Product → ModifierGroup и ModifierOption удаляются каскадно."""
    group_id = group.id
    option_id = option.id
    c = _client_as(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/product/{product.id}")
        assert resp.status_code == 204, resp.text
    finally:
        app.dependency_overrides.clear()

    assert db.query(ModifierGroup).filter(ModifierGroup.id == group_id).first() is None
    assert db.query(ModifierOption).filter(ModifierOption.id == option_id).first() is None
