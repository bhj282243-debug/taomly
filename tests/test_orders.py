"""
tests/test_orders.py — Order creation, status transitions, tenant isolation

Покрывает:
  - Создание takeaway заказа
  - Создание delivery заказа (с адресом)
  - Delivery без адреса → 422
  - dine_in без table_id → 422
  - Цена считается на сервере (нельзя подменить)
  - Недоступный продукт → 404
  - Продукт чужого ресторана → 404 (IDOR защита)
  - Смена статуса заказа
  - Невалидный переход статуса → 400
  - GET заказов другого ресторана → tenant isolation
"""

import pytest


# ──────────────────────────────────────────
# TEST 11: Create takeaway order
# ──────────────────────────────────────────
@pytest.mark.integration
def test_create_takeaway_order(client, product):
    resp = client.post("/api/orders/", json={
        "order_type": "takeaway",
        "client_name": "Алишер",
        "client_phone": "+998901234567",
        "items": [{"product_id": product.id, "quantity": 2}],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "new"
    assert data["order_type"] == "takeaway"
    # total_amount = цена из БД (15000) * 2, не из запроса
    assert data["total_amount"] == 30000
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Самса"
    assert "updated_at" in data  # M-3 fix проверяем


# ──────────────────────────────────────────
# TEST 12: Create delivery order — with address
# ──────────────────────────────────────────
@pytest.mark.integration
def test_create_delivery_order_with_address(client, product):
    resp = client.post("/api/orders/", json={
        "order_type": "delivery",
        "client_name": "Камол",
        "client_phone": "+998901234568",
        "address": "ул. Навои, 15",
        "items": [{"product_id": product.id, "quantity": 1}],
    })
    assert resp.status_code == 201
    assert resp.json()["order_type"] == "delivery"
    assert resp.json()["address"] == "ул. Навои, 15"


# ──────────────────────────────────────────
# TEST 13: Delivery without address → 422
# ──────────────────────────────────────────
@pytest.mark.integration
def test_create_delivery_without_address_fails(client, product):
    resp = client.post("/api/orders/", json={
        "order_type": "delivery",
        "items": [{"product_id": product.id, "quantity": 1}],
    })
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    assert any("address" in str(e).lower() or "доставк" in str(e).lower() for e in errors)


# ──────────────────────────────────────────
# TEST 14: dine_in without table_id → 422
# ──────────────────────────────────────────
@pytest.mark.integration
def test_create_dine_in_without_table_fails(client, product):
    resp = client.post("/api/orders/", json={
        "order_type": "dine_in",
        "items": [{"product_id": product.id, "quantity": 1}],
    })
    assert resp.status_code == 422


# ──────────────────────────────────────────
# TEST 15: Server-side price calculation (IDOR guard)
# ──────────────────────────────────────────
@pytest.mark.security
def test_order_price_from_db_not_client(client, product):
    """
    Клиент не может подменить цену.
    total_amount всегда считается из БД.
    """
    resp = client.post("/api/orders/", json={
        "order_type": "takeaway",
        "items": [{"product_id": product.id, "quantity": 1}],
        # Нет поля price в OrderItemCreate — сервер берёт из БД
    })
    assert resp.status_code == 201
    # Цена из БД: 15000, не 1 (подмена невозможна)
    assert resp.json()["total_amount"] == 15000


# ──────────────────────────────────────────
# TEST 16: Unavailable product → 404
# ──────────────────────────────────────────
@pytest.mark.integration
def test_create_order_unavailable_product(client, product_unavailable):
    resp = client.post("/api/orders/", json={
        "order_type": "takeaway",
        "items": [{"product_id": product_unavailable.id, "quantity": 1}],
    })
    assert resp.status_code == 404


# ──────────────────────────────────────────
# TEST 17: IDOR — product from another restaurant → 404
# ──────────────────────────────────────────
@pytest.mark.security
def test_create_order_with_foreign_product(client, db, agency2, restaurant2):
    """
    Критический тест: пользователь ресторана A не может заказать
    продукт ресторана B. create_order должен вернуть 404.
    """
    from models import Category, Product

    # Создаём продукт в ресторане B
    cat2 = Category(restaurant_id=restaurant2.id, name="Категория 2", sort_order=1)
    db.add(cat2)
    db.flush()

    foreign_product = Product(
        restaurant_id=restaurant2.id,
        category_id=cat2.id,
        name="Чужое блюдо",
        price=99999,
        is_available=True,
    )
    db.add(foreign_product)
    db.flush()

    # tg_user из client fixture принадлежит restaurant, не restaurant2
    resp = client.post("/api/orders/", json={
        "order_type": "takeaway",
        "items": [{"product_id": foreign_product.id, "quantity": 1}],
    })
    # Должен вернуть 404: продукт не найден В ЭТОМ ресторане
    assert resp.status_code == 404


# ──────────────────────────────────────────
# TEST 18: Status transition valid
# ──────────────────────────────────────────
@pytest.mark.integration
def test_order_status_transition_valid(client, db, product, restaurant):
    from models import Order, OrderItem

    # Создаём заказ напрямую в БД
    order = Order(
        restaurant_id=restaurant.id,
        client_telegram_id=111111111,
        order_type="takeaway",
        total_amount=15000,
        status="new",
    )
    db.add(order)
    db.flush()

    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        name="Самса",
        price=15000,
        quantity=1,
    )
    db.add(item)
    db.flush()

    # new → accepted: валидный переход
    resp = client.patch(f"/api/orders/{order.id}/status", json={"status": "accepted"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


# ──────────────────────────────────────────
# TEST 19: Status transition invalid → 400
# ──────────────────────────────────────────
@pytest.mark.integration
def test_order_status_transition_invalid(client, db, restaurant):
    from models import Order

    order = Order(
        restaurant_id=restaurant.id,
        client_telegram_id=111111111,
        order_type="takeaway",
        total_amount=10000,
        status="completed",  # завершённый заказ
    )
    db.add(order)
    db.flush()

    # completed → new: невалидный переход
    resp = client.patch(f"/api/orders/{order.id}/status", json={"status": "new"})
    assert resp.status_code == 400


# ──────────────────────────────────────────
# TEST 20: Tenant isolation — GET orders
# ──────────────────────────────────────────
@pytest.mark.security
def test_get_orders_tenant_isolation(client, db, restaurant, restaurant2):
    """
    Ресторан A не видит заказы ресторана B.
    """
    from models import Order

    # Заказ ресторана B
    order_b = Order(
        restaurant_id=restaurant2.id,
        client_telegram_id=999999,
        order_type="takeaway",
        total_amount=20000,
        status="new",
    )
    db.add(order_b)
    db.flush()

    # client fixture использует tg_user ресторана A
    resp = client.get(f"/api/orders/restaurant/{restaurant.id}")
    assert resp.status_code == 200

    order_ids = [o["id"] for o in resp.json()]
    # Заказ ресторана B не должен появиться в ответе ресторана A
    assert order_b.id not in order_ids
