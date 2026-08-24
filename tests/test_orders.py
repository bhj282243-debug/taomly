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
    assert data["status"] == "accepted"   # заказ создаётся сразу accepted (авто-подтверждение)
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
    # 400 Bad Request: продукт существует, но недоступен
    assert resp.status_code == 400
    assert "недоступен" in resp.json()["detail"]


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
def test_order_status_transition_valid(client, db, product, restaurant, location):
    from models import Order, OrderItem

    # Создаём заказ напрямую в БД
    order = Order(
        restaurant_id=restaurant.id,
        location_id=location.id,   # S1-3: required
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
def test_order_status_transition_invalid(client, db, restaurant, location):
    from models import Order

    order = Order(
        restaurant_id=restaurant.id,
        location_id=location.id,   # S1-3: required
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
def test_get_orders_tenant_isolation(client, db, restaurant, restaurant2, location2):
    """
    Ресторан A не видит заказы ресторана B.
    """
    from models import Order

    # Заказ ресторана B — location2 принадлежит restaurant2
    order_b = Order(
        restaurant_id=restaurant2.id,
        location_id=location2.id,  # S1-3: required
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


# ══════════════════════════════════════════
# TASK 1A — новые тесты
# ══════════════════════════════════════════

# ──────────────────────────────────────────
# TEST 21: Product changed to unavailable after page load — still rejected
# ──────────────────────────────────────────
@pytest.mark.security
def test_create_order_product_made_unavailable_after_load(client, db, product):
    """
    Race condition: клиент загрузил меню (продукт был available), затем
    администратор выключил продукт. Повторный заказ должен быть отклонён.

    Проверяет серверную валидацию is_available — не доверяем состоянию клиента.
    """
    # Сначала убеждаемся что продукт доступен
    assert product.is_available is True

    # Администратор выключает продукт
    product.is_available = False
    db.flush()

    # Клиент пытается заказать (как будто не знает об изменении)
    resp = client.post("/api/orders/", json={
        "order_type": "takeaway",
        "items": [{"product_id": product.id, "quantity": 1}],
    })
    assert resp.status_code == 400
    assert "недоступен" in resp.json()["detail"]


# ──────────────────────────────────────────
# TEST 22: Product from another tenant rejected even if available
# ──────────────────────────────────────────
@pytest.mark.security
def test_create_order_cross_tenant_product_rejected(client, db, agency2, restaurant2):
    """
    Продукт из ресторана B (доступный) нельзя заказать через ресторан A.
    Tenant isolation: проверяется Product.restaurant_id == current_restaurant.id
    """
    from models import Category, Product

    cat2 = Category(restaurant_id=restaurant2.id, name="Menu B", sort_order=1)
    db.add(cat2)
    db.flush()

    product_b = Product(
        restaurant_id=restaurant2.id,
        category_id=cat2.id,
        name="Чужое блюдо",
        price=1,           # цена 1 — если бы прошло, был бы признак утечки
        is_available=True,
    )
    db.add(product_b)
    db.flush()

    resp = client.post("/api/orders/", json={
        "order_type": "takeaway",
        "items": [{"product_id": product_b.id, "quantity": 1}],
    })
    assert resp.status_code == 404
    # total_amount не должен использовать цену 1 из чужого ресторана
    assert "total_amount" not in resp.json()


# ──────────────────────────────────────────
# TEST 23: Server ignores client-supplied price
# ──────────────────────────────────────────
@pytest.mark.security
def test_order_total_uses_server_price_not_client(client, product, product2):
    """
    Клиент не может подменить цену через тело запроса.
    total_amount считается строго из БД: product.price * quantity.
    product  = 15000, product2 = 30000.
    """
    resp = client.post("/api/orders/", json={
        "order_type": "takeaway",
        "items": [
            {"product_id": product.id,  "quantity": 2},
            {"product_id": product2.id, "quantity": 1},
        ],
    })
    assert resp.status_code == 201
    # 15000*2 + 30000*1 = 60000
    assert resp.json()["total_amount"] == 60000


# ──────────────────────────────────────────
# TEST 24: Nonexistent product_id → 404
# ──────────────────────────────────────────
@pytest.mark.integration
def test_create_order_nonexistent_product(client):
    """
    product_id которого нет в БД → 404.
    Защита от прямых API-запросов с произвольными ID.
    """
    resp = client.post("/api/orders/", json={
        "order_type": "takeaway",
        "items": [{"product_id": 999999999, "quantity": 1}],
    })
    assert resp.status_code == 404


# ──────────────────────────────────────────
# TEST 25: format_price — UZS
# ──────────────────────────────────────────
@pytest.mark.unit
def test_format_price_uzs():
    from utils import format_price
    result = format_price(25000, "UZS")
    # 25 000 so'm — пробел как разделитель тысяч (неразрывный \u00a0)
    assert "25" in result
    assert "000" in result
    assert "so" in result


# ──────────────────────────────────────────
# TEST 26: format_price — KZT
# ──────────────────────────────────────────
@pytest.mark.unit
def test_format_price_kzt():
    from utils import format_price
    result = format_price(25000, "KZT")
    assert "25" in result
    assert "\u20b8" in result   # ₸


# ──────────────────────────────────────────
# TEST 27: format_price — RUB
# ──────────────────────────────────────────
@pytest.mark.unit
def test_format_price_rub():
    from utils import format_price
    result = format_price(1500, "RUB")
    assert "1" in result
    assert "500" in result
    assert "\u20bd" in result   # ₽


# ──────────────────────────────────────────
# TEST 28: format_price — USD
# ──────────────────────────────────────────
@pytest.mark.unit
def test_format_price_usd():
    from utils import format_price
    result = format_price(25, "USD")
    assert result == "$25.00"


# ──────────────────────────────────────────
# TEST 29: format_price — TRY
# ──────────────────────────────────────────
@pytest.mark.unit
def test_format_price_try():
    from utils import format_price
    result = format_price(25, "TRY")
    assert "\u20ba" in result   # ₺
    assert "25.00" in result


# ──────────────────────────────────────────
# TEST 30: format_price — AED
# ──────────────────────────────────────────
@pytest.mark.unit
def test_format_price_aed():
    from utils import format_price
    result = format_price(25, "AED")
    assert result == "AED 25.00"


# ──────────────────────────────────────────
# TEST 31: format_price — unknown currency fallback to UZS
# ──────────────────────────────────────────
@pytest.mark.unit
def test_format_price_unknown_currency_fallback():
    from utils import format_price
    # Неизвестная валюта → fallback UZS, не ломается
    result = format_price(1000, "XYZ")
    assert "1" in result
    assert "000" in result
    assert "so" in result


# ──────────────────────────────────────────
# TEST 32: format_price — None currency → UZS default
# ──────────────────────────────────────────
@pytest.mark.unit
def test_format_price_none_currency():
    from utils import format_price
    result = format_price(5000, None)
    assert "so" in result


# ──────────────────────────────────────────
# TEST 33: Restaurant.currency field exists and defaults to UZS
# ──────────────────────────────────────────
@pytest.mark.integration
def test_restaurant_currency_default(restaurant):
    assert restaurant.currency == "UZS"


# ──────────────────────────────────────────
# TEST 34: Restaurant with RUB currency — price format in order error
# ──────────────────────────────────────────
@pytest.mark.integration
def test_order_min_amount_error_uses_restaurant_currency(client, db, restaurant, location):
    """
    Сообщение об ошибке минимальной суммы заказа использует валюту ресторана,
    а не hardcoded so'm.

    S1-7: min_order_amount и currency читаются из Location (source of truth).
    Тест обновляет Location напрямую, а также Restaurant для backward compat.
    """
    from models import Category, Product

    # S1-7: устанавливаем min_order_amount и currency на Location (source of truth)
    location.min_order_amount = 500
    location.currency = "RUB"
    # Backward compat: синхронизируем Restaurant
    restaurant.min_order_amount = 500
    restaurant.currency = "RUB"
    db.flush()

    cat = Category(restaurant_id=restaurant.id, name="Тест", sort_order=99)
    db.add(cat)
    db.flush()

    cheap = Product(
        restaurant_id=restaurant.id,
        category_id=cat.id,
        name="Дешёвое блюдо",
        price=10,
        is_available=True,
    )
    db.add(cheap)
    db.flush()

    resp = client.post("/api/orders/", json={
        "order_type": "delivery",
        "address": "ул. Тестовая, 1",
        "items": [{"product_id": cheap.id, "quantity": 1}],
    })
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    # Должен содержать символ рубля, не so'm
    assert "\u20bd" in detail   # ₽
    assert "so'm" not in detail


# ──────────────────────────────────────────
# TEST 35: RestaurantPublicResponse includes currency
# ──────────────────────────────────────────
@pytest.mark.integration
def test_restaurant_public_response_includes_currency(client, db, restaurant):
    """
    GET /api/restaurants/{slug} возвращает поле currency.
    Фронтенд использует его для форматирования цен.
    """
    from models import Category
    # Убедимся что у ресторана есть хотя бы одна категория (иначе пустой ответ не дойдёт)
    cat = Category(restaurant_id=restaurant.id, name="Тест", sort_order=99)
    db.add(cat)
    db.flush()

    resp = client.get(f"/api/restaurants/{restaurant.slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert "currency" in data
    assert data["currency"] == "UZS"
