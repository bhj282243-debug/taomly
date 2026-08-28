"""
tests/test_s2_5_variant_orders.py — S2-5 Variant Order Engine Tests

Покрытие:
  - Legacy products (CASE A): без вариантов → product.price используется
  - Variant products (CASE B/C): с вариантами → обязательный variant_id
  - Tenant isolation: cross-product, cross-restaurant variant injection
  - Server-side price: клиентская цена не принимается
  - Snapshot behaviour: price не меняется после изменения variant.price
  - Public API: variants в ответе, inactive скрыты
  - Availability: product.is_available + variant.is_active
  - Regression: все существующие order тесты не сломаны

CI Gate: все тесты должны PASS без новых регрессий.
"""

import pytest
from fastapi.testclient import TestClient

from models import Location, Order, OrderItem, Product, ProductVariant, Restaurant, Category


# ──────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def product_no_variants(db, restaurant, category) -> Product:
    """Legacy-продукт: price=20000, без вариантов."""
    p = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Самса Обычная",
        price=20000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def product_with_variants(db, restaurant, category) -> Product:
    """Variant-продукт: price=None, два варианта."""
    p = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Плов",
        price=None,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def variant_small(db, product_with_variants) -> ProductVariant:
    v = ProductVariant(
        product_id=product_with_variants.id,
        name="Маленький",
        price=15000,
        sort_order=1,
        is_active=True,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def variant_large(db, product_with_variants) -> ProductVariant:
    v = ProductVariant(
        product_id=product_with_variants.id,
        name="Большой",
        price=25000,
        sort_order=2,
        is_active=True,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def variant_inactive(db, product_with_variants) -> ProductVariant:
    v = ProductVariant(
        product_id=product_with_variants.id,
        name="Средний (неактивный)",
        price=20000,
        sort_order=3,
        is_active=False,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def product_unavailable_with_variant(db, restaurant, category) -> Product:
    """Недоступный продукт с вариантом — заказ должен быть отклонён."""
    p = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Недоступное блюдо с вариантом",
        price=None,
        is_available=False,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def variant_for_unavailable(db, product_unavailable_with_variant) -> ProductVariant:
    v = ProductVariant(
        product_id=product_unavailable_with_variant.id,
        name="Стандарт",
        price=30000,
        sort_order=1,
        is_active=True,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def foreign_product(db, restaurant2, category) -> Product:
    """Продукт другого ресторана (tenant B)."""
    # Нужна категория другого ресторана
    cat2 = Category(restaurant_id=restaurant2.id, name="Чужие блюда", sort_order=1)
    db.add(cat2)
    db.flush()
    p = Product(
        restaurant_id=restaurant2.id,
        category_id=cat2.id,
        name="Чужой продукт",
        price=None,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def foreign_variant(db, foreign_product) -> ProductVariant:
    """Вариант продукта другого ресторана (tenant B)."""
    v = ProductVariant(
        product_id=foreign_product.id,
        name="Вариант чужого",
        price=99000,
        sort_order=1,
        is_active=True,
    )
    db.add(v)
    db.flush()
    return v


# helper: стандартный takeaway order payload
def _takeaway_payload(items: list) -> dict:
    return {
        "order_type": "takeaway",
        "items": items,
    }


# ──────────────────────────────────────────────────────────────────────────────
# LEGACY TESTS (CASE A)
# ──────────────────────────────────────────────────────────────────────────────

def test_legacy_product_no_variant_id_returns_201(client, product_no_variants):
    """TEST 1: Legacy продукт без variant_id → 201 Created."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_no_variants.id, "quantity": 1}
    ]))
    assert resp.status_code == 201


def test_legacy_product_uses_product_price(client, product_no_variants, db):
    """TEST 2: OrderItem.price == product.price (из БД, не от клиента)."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_no_variants.id, "quantity": 2}
    ]))
    assert resp.status_code == 201
    data = resp.json()
    item = data["items"][0]
    assert item["price"] == product_no_variants.price
    assert data["total_amount"] == product_no_variants.price * 2


def test_legacy_order_item_variant_id_is_null(client, product_no_variants, db):
    """TEST 3: OrderItem.variant_id IS NULL для legacy заказа."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_no_variants.id, "quantity": 1}
    ]))
    assert resp.status_code == 201
    order_id = resp.json()["id"]
    order_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).first()
    assert order_item is not None
    assert order_item.variant_id is None


def test_legacy_order_item_variant_name_is_null(client, product_no_variants, db):
    """TEST 4: OrderItem.variant_name IS NULL для legacy заказа."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_no_variants.id, "quantity": 1}
    ]))
    assert resp.status_code == 201
    order_id = resp.json()["id"]
    order_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).first()
    assert order_item.variant_name is None


# ──────────────────────────────────────────────────────────────────────────────
# VARIANT ORDER TESTS (CASE C — valid)
# ──────────────────────────────────────────────────────────────────────────────

def test_variant_product_valid_variant_returns_201(
    client, product_with_variants, variant_small
):
    """TEST 5: Продукт с вариантами + valid variant_id → 201."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_with_variants.id, "quantity": 1, "variant_id": variant_small.id}
    ]))
    assert resp.status_code == 201


def test_variant_order_price_equals_variant_price(
    client, product_with_variants, variant_small
):
    """TEST 6: OrderItem.price == variant.price (не product.price)."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_with_variants.id, "quantity": 1, "variant_id": variant_small.id}
    ]))
    assert resp.status_code == 201
    data = resp.json()
    item = data["items"][0]
    assert item["price"] == variant_small.price  # 15000


def test_variant_order_item_name_equals_product_name(
    client, product_with_variants, variant_large
):
    """TEST 7: OrderItem.name == product.name (не "Плов — Большой")."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_with_variants.id, "quantity": 1, "variant_id": variant_large.id}
    ]))
    assert resp.status_code == 201
    item = resp.json()["items"][0]
    assert item["name"] == product_with_variants.name  # "Плов"
    # НЕ должно быть "Плов — Большой"
    assert "—" not in item["name"]


def test_variant_order_item_variant_name_is_snapshot(
    client, product_with_variants, variant_large
):
    """TEST 8: OrderItem.variant_name == variant.name (отдельный snapshot)."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_with_variants.id, "quantity": 1, "variant_id": variant_large.id}
    ]))
    assert resp.status_code == 201
    item = resp.json()["items"][0]
    assert item["variant_name"] == variant_large.name  # "Большой"


def test_variant_id_stored_correctly_in_db(
    client, product_with_variants, variant_small, db
):
    """TEST 9: variant_id корректно хранится в OrderItem.variant_id."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_with_variants.id, "quantity": 1, "variant_id": variant_small.id}
    ]))
    assert resp.status_code == 201
    order_id = resp.json()["id"]
    order_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).first()
    assert order_item.variant_id == variant_small.id


# ──────────────────────────────────────────────────────────────────────────────
# REQUIRED VARIANT TESTS (CASE B)
# ──────────────────────────────────────────────────────────────────────────────

def test_variant_product_without_variant_id_returns_400(
    client, product_with_variants, variant_small
):
    """TEST 10: Продукт с вариантами + нет variant_id → 400."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_with_variants.id, "quantity": 1}
        # variant_id не передан
    ]))
    assert resp.status_code == 400
    assert "вариант" in resp.json()["detail"].lower()


# ──────────────────────────────────────────────────────────────────────────────
# INVALID VARIANT TESTS
# ──────────────────────────────────────────────────────────────────────────────

def test_nonexistent_variant_rejected(client, product_with_variants, variant_small):
    """TEST 11: Несуществующий variant_id → rejected (400)."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_with_variants.id, "quantity": 1, "variant_id": 999999999}
    ]))
    assert resp.status_code == 400


def test_variant_belonging_to_another_product_rejected(
    client, product_with_variants, product_no_variants, variant_small, db
):
    """TEST 12: variant принадлежит другому продукту → rejected.
    product_id=product_no_variants, variant_id=variant_of_product_with_variants → 400.
    """
    # product_no_variants не имеет вариантов, но даже если передать variant_id
    # чужого продукта — должен быть rejected (после добавления активного варианта к no_variants)
    # Создаём вариант для product_no_variants чтобы активировать CASE B/C
    own_variant = ProductVariant(
        product_id=product_no_variants.id,
        name="Маленький",
        price=5000,
        sort_order=1,
        is_active=True,
    )
    db.add(own_variant)
    db.flush()

    # Передаём variant_id другого продукта (cross-product injection)
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {
            "product_id": product_no_variants.id,
            "quantity": 1,
            "variant_id": variant_small.id,  # variant принадлежит product_with_variants!
        }
    ]))
    assert resp.status_code == 400


def test_inactive_variant_rejected(
    client, product_with_variants, variant_inactive, variant_small
):
    """TEST 13: Неактивный variant → rejected (400)."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {
            "product_id": product_with_variants.id,
            "quantity": 1,
            "variant_id": variant_inactive.id,  # is_active=False
        }
    ]))
    assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────────────
# TENANT ISOLATION
# ──────────────────────────────────────────────────────────────────────────────

def test_foreign_tenant_product_variant_rejected(
    client, product_with_variants, foreign_variant, variant_small
):
    """TEST 14: Вариант другого tenant → rejected.
    product принадлежит restaurant A, variant принадлежит restaurant B (через foreign_product).
    """
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {
            "product_id": product_with_variants.id,
            "quantity": 1,
            "variant_id": foreign_variant.id,  # variant_of_restaurant_B
        }
    ]))
    assert resp.status_code == 400


def test_product_a_with_variant_b_rejected(
    client, product_with_variants, foreign_variant, variant_small
):
    """TEST 15: product_id=A, variant_id=B (другой продукт) → rejected.
    Это дублирует логику TEST 14 с явной формулировкой инварианта.
    product_with_variants принадлежит restaurant A.
    foreign_variant принадлежит foreign_product который принадлежит restaurant B.
    Запрос: product_id=A + variant_id=B → должен быть отклонён.
    """
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {
            "product_id": product_with_variants.id,
            "quantity": 1,
            "variant_id": foreign_variant.id,
        }
    ]))
    assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────────────
# PRICE SECURITY
# ──────────────────────────────────────────────────────────────────────────────

def test_client_supplied_price_cannot_override_variant_price(
    client, product_with_variants, variant_small
):
    """TEST 16: Клиент не может подменить цену.
    OrderItemCreate не имеет поля price — сервер всегда берёт из variant.price.
    Даже если клиент каким-то образом передаст price — он игнорируется Pydantic.
    """
    # Pydantic игнорирует неизвестные поля по умолчанию
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {
            "product_id": product_with_variants.id,
            "quantity": 1,
            "variant_id": variant_small.id,
            "price": 1,  # попытка подмены цены — должна быть проигнорирована
        }
    ]))
    assert resp.status_code == 201
    item = resp.json()["items"][0]
    # Цена должна быть из variant.price, не из клиентского поля
    assert item["price"] == variant_small.price  # 15000, не 1


def test_variant_price_snapshot_survives_later_update(
    client, product_with_variants, variant_small, db
):
    """TEST 17: Snapshot цены не меняется после изменения variant.price.
    Цена в OrderItem — исторический snapshot на момент заказа.
    """
    original_price = variant_small.price  # 15000

    # Создаём заказ
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_with_variants.id, "quantity": 1, "variant_id": variant_small.id}
    ]))
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    # Меняем цену варианта
    variant_small.price = 99999
    db.flush()
    db.commit()

    # Проверяем что OrderItem.price не изменился (snapshot)
    order_item = db.query(OrderItem).filter(OrderItem.order_id == order_id).first()
    assert order_item.price == original_price  # 15000, не 99999


# ──────────────────────────────────────────────────────────────────────────────
# AVAILABILITY
# ──────────────────────────────────────────────────────────────────────────────

def test_unavailable_product_with_valid_variant_rejected(
    client, product_unavailable_with_variant, variant_for_unavailable
):
    """TEST 18: product.is_available=False + активный variant → rejected (400).
    Product availability проверяется раньше variant validation.
    """
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {
            "product_id": product_unavailable_with_variant.id,
            "quantity": 1,
            "variant_id": variant_for_unavailable.id,
        }
    ]))
    assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────────────────────

def test_public_menu_legacy_product_has_price_and_empty_variants(
    client, restaurant, product_no_variants
):
    """TEST 19: Legacy продукт → price=int, variants=[]."""
    resp = client.get(f"/api/v1/restaurants/{restaurant.slug}/menu")
    assert resp.status_code == 200
    data = resp.json()
    all_products = [
        p
        for cat in data.get("categories", [])
        for p in cat.get("products", [])
    ]
    target = next((p for p in all_products if p["id"] == product_no_variants.id), None)
    assert target is not None
    assert target["price"] == product_no_variants.price
    assert target["variants"] == []


def test_public_menu_variant_product_has_null_price_and_variants(
    client, restaurant, product_with_variants, variant_small, variant_large
):
    """TEST 20: Variant продукт → price=null, variants=[{id,name,price}]."""
    resp = client.get(f"/api/v1/restaurants/{restaurant.slug}/menu")
    assert resp.status_code == 200
    data = resp.json()
    all_products = [
        p
        for cat in data.get("categories", [])
        for p in cat.get("products", [])
    ]
    target = next((p for p in all_products if p["id"] == product_with_variants.id), None)
    assert target is not None
    assert target["price"] is None
    variant_ids = [v["id"] for v in target["variants"]]
    assert variant_small.id in variant_ids
    assert variant_large.id in variant_ids


def test_public_menu_inactive_variants_excluded(
    client, restaurant, product_with_variants, variant_small, variant_inactive
):
    """TEST 21: Неактивные варианты не попадают в public menu."""
    resp = client.get(f"/api/v1/restaurants/{restaurant.slug}/menu")
    assert resp.status_code == 200
    data = resp.json()
    all_products = [
        p
        for cat in data.get("categories", [])
        for p in cat.get("products", [])
    ]
    target = next((p for p in all_products if p["id"] == product_with_variants.id), None)
    assert target is not None
    variant_ids = [v["id"] for v in target["variants"]]
    # Активный вариант виден
    assert variant_small.id in variant_ids
    # Неактивный вариант скрыт
    assert variant_inactive.id not in variant_ids


# ──────────────────────────────────────────────────────────────────────────────
# TEST #8: Product.price=None + no active variants → safe 400
# ──────────────────────────────────────────────────────────────────────────────

def test_product_null_price_no_variants_safe_400(client, db, restaurant, category):
    """TEST #8: Продукт без цены и без активных вариантов → HTTP 400 (не TypeError).
    Защита от аномального состояния: price=None + variants=[] → корректный отказ.
    """
    anomalous_product = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Аномальный продукт",
        price=None,       # NULL price
        is_available=True,
        # нет вариантов
    )
    db.add(anomalous_product)
    db.flush()

    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": anomalous_product.id, "quantity": 1}
    ]))
    # Должен быть 400, не 500 (TypeError)
    assert resp.status_code == 400
    assert "цены" in resp.json()["detail"].lower() or "вариант" in resp.json()["detail"].lower()


# ──────────────────────────────────────────────────────────────────────────────
# TEST #9: Multiple items + one invalid item → no partial order
# ──────────────────────────────────────────────────────────────────────────────

def test_multiple_items_one_invalid_no_partial_order(
    client, product_no_variants, product_with_variants, variant_small, db
):
    """TEST #9: Два items — первый валидный, второй невалидный (variant_id не передан).
    Ожидается HTTP 400. Order НЕ создаётся. Item 1 НЕ сохраняется.
    Проверяем transaction atomicity.
    """
    orders_before = db.query(Order).count()
    items_before = db.query(OrderItem).count()

    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        # Item 1: valid legacy item
        {"product_id": product_no_variants.id, "quantity": 1},
        # Item 2: invalid — variant product without variant_id
        {"product_id": product_with_variants.id, "quantity": 1},
    ]))

    assert resp.status_code == 400

    # Убеждаемся что ни Order ни OrderItem не были сохранены
    orders_after = db.query(Order).count()
    items_after = db.query(OrderItem).count()

    db.expire_all()  # force reload from DB
    assert db.query(Order).count() == orders_before
    assert db.query(OrderItem).count() == items_before


# ──────────────────────────────────────────────────────────────────────────────
# REGRESSION: existing order flow must not break
# ──────────────────────────────────────────────────────────────────────────────

def test_regression_existing_product_with_price_still_works(client, product):
    """REGRESSION: существующий продукт (price=15000) из conftest работает."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product.id, "quantity": 2}
    ]))
    assert resp.status_code == 201
    data = resp.json()
    assert data["total_amount"] == product.price * 2


def test_regression_total_amount_calculated_server_side(client, product, product2):
    """REGRESSION: total_amount вычисляется сервером из БД (не от клиента)."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product.id, "quantity": 2},
        {"product_id": product2.id, "quantity": 1},
    ]))
    assert resp.status_code == 201
    expected_total = product.price * 2 + product2.price * 1
    assert resp.json()["total_amount"] == expected_total


def test_regression_order_item_response_includes_variant_name_null_for_legacy(
    client, product
):
    """REGRESSION: OrderItemResponse.variant_name=null для legacy заказов (backward compat)."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product.id, "quantity": 1}
    ]))
    assert resp.status_code == 201
    item = resp.json()["items"][0]
    # variant_name должен присутствовать в ответе (nullable)
    assert "variant_name" in item
    assert item["variant_name"] is None


def test_regression_multi_item_order_with_variant_and_legacy(
    client, product_no_variants, product_with_variants, variant_large
):
    """REGRESSION: Смешанный заказ — legacy + variant items в одном order."""
    resp = client.post("/api/v1/orders/", json=_takeaway_payload([
        {"product_id": product_no_variants.id, "quantity": 1},
        {"product_id": product_with_variants.id, "quantity": 2, "variant_id": variant_large.id},
    ]))
    assert resp.status_code == 201
    data = resp.json()
    expected_total = product_no_variants.price * 1 + variant_large.price * 2
    assert data["total_amount"] == expected_total
    items = {i["name"]: i for i in data["items"]}
    assert items[product_no_variants.name]["variant_name"] is None
    assert items[product_with_variants.name]["variant_name"] == variant_large.name
