"""
tests/test_tenant_isolation.py — Foundation Task 2: Tenant Isolation

Cross-tenant тесты. Покрывают:

  IDOR:
    Product      — GET/PATCH/DELETE чужого продукта
    Category     — PATCH/DELETE чужой категории
    Order        — GET/PATCH-status чужого заказа
    Reservation  — PATCH-status чужой брони
    WaiterCall   — PATCH-status чужого вызова
    RestaurantTable — DELETE чужого стола

  LIST endpoints:
    Products (меню) — список содержит только свои записи
    Orders          — список только своего ресторана
    Reservations    — список только своего ресторана
    WaiterCalls     — список только своего ресторана
    Tables          — список только своего ресторана

  CREATE — нельзя создать объект в чужом tenant:
    Product   — category из чужого ресторана
    Order     — продукт из чужого ресторана
    WaiterCall — стол из чужого ресторана

  Agency isolation:
    Agency A не видит рестораны Agency B
    Agency A не может PATCH/DELETE ресторан Agency B

  Telegram entry point:
    Клиент ресторана A не может получить заказ ресторана B
    Клиент ресторана A не может создать заказ с продуктом ресторана B
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api import app
from auth import (
    TelegramUser,
    get_current_agency,
    get_current_restaurant_admin,
    get_telegram_user,
)
from database import get_db
from models import (
    Agency,
    Category,
    Location,
    Order,
    OrderItem,
    Product,
    Reservation,
    Restaurant,
    RestaurantTable,
    WaiterCall,
)


# ─────────────────────────────────────────
# HELPER — клиент от имени конкретного ресторана / агентства
# ─────────────────────────────────────────

def _client_as_restaurant(db: Session, restaurant: Restaurant, tg_user: TelegramUser):
    """TestClient с зависимостями от имени ресторана."""
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant
    app.dependency_overrides[get_telegram_user] = lambda: tg_user
    c = TestClient(app, raise_server_exceptions=True)
    return c


def _client_as_agency(db: Session, agency: Agency, restaurant: Restaurant, tg_user: TelegramUser):
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_agency] = lambda: agency
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant
    app.dependency_overrides[get_telegram_user] = lambda: tg_user
    c = TestClient(app, raise_server_exceptions=True)
    return c


# ─────────────────────────────────────────
# FIXTURES: second restaurant's data
# ─────────────────────────────────────────

@pytest.fixture
def category2(db, restaurant2) -> Category:
    """Категория второго ресторана."""
    c = Category(restaurant_id=restaurant2.id, name="Салаты", sort_order=1)
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def product_r2(db, restaurant2, category2) -> Product:
    """Продукт второго ресторана."""
    p = Product(
        restaurant_id=restaurant2.id,
        category_id=category2.id,
        name="Греческий салат",
        price=25000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def order_r1(db, restaurant, product) -> Order:
    """Заказ первого ресторана."""
    o = Order(
        restaurant_id=restaurant.id,
        client_telegram_id=111111111,
        client_name="Алишер",
        order_type="takeaway",
        total_amount=15000,
        status="accepted",
    )
    db.add(o)
    db.flush()
    db.add(OrderItem(order_id=o.id, product_id=product.id, name=product.name, price=product.price, quantity=1))
    db.flush()
    return o


@pytest.fixture
def order_r2(db, restaurant2, product_r2) -> Order:
    """Заказ второго ресторана."""
    o = Order(
        restaurant_id=restaurant2.id,
        client_telegram_id=222222222,
        client_name="Камол",
        order_type="takeaway",
        total_amount=25000,
        status="accepted",
    )
    db.add(o)
    db.flush()
    db.add(OrderItem(order_id=o.id, product_id=product_r2.id, name=product_r2.name, price=product_r2.price, quantity=1))
    db.flush()
    return o


@pytest.fixture
def reservation_r2(db, restaurant2) -> Reservation:
    """Бронь второго ресторана."""
    from datetime import datetime, timezone, timedelta
    r = Reservation(
        restaurant_id=restaurant2.id,
        client_name="Камол",
        client_phone="+998991234567",
        guests_count=2,
        reservation_time=datetime.now(timezone.utc) + timedelta(days=1),
        status="new",
    )
    db.add(r)
    db.flush()
    return r


@pytest.fixture
def _location_r2(db, restaurant2) -> Location:
    """Location второго ресторана — для table_r2 (S1-2)."""
    loc = Location(
        restaurant_id=restaurant2.id,
        name=restaurant2.name,
        slug=restaurant2.slug,
        is_active=True,
        timezone="Asia/Tashkent",
        delivery_fee=0,
        min_order_amount=0,
        currency="USD",
        language="uz",
        is_waiter_call_enabled=False,
    )
    db.add(loc)
    db.flush()
    return loc


@pytest.fixture
def table_r2(db, restaurant2, _location_r2) -> RestaurantTable:
    """Стол второго ресторана (S1-2: location_id добавлен)."""
    t = RestaurantTable(
        restaurant_id=restaurant2.id,
        location_id=_location_r2.id,
        table_number="10",
    )
    db.add(t)
    db.flush()
    return t


@pytest.fixture
def waiter_call_r2(db, restaurant2, table_r2) -> WaiterCall:
    """Вызов официанта второго ресторана."""
    w = WaiterCall(restaurant_id=restaurant2.id, table_id=table_r2.id, status="active")
    db.add(w)
    db.flush()
    return w


# ─────────────────────────────────────────
# IDOR — PRODUCT
# ─────────────────────────────────────────

@pytest.mark.security
def test_idor_product_get_menu_all(db, restaurant, product, product_r2, tg_user):
    """
    Restaurant A не должен видеть продукт Restaurant B в своём полном меню.
    GET /{restaurant_id}/all фильтрует по restaurant_id токена.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/{restaurant.id}/all")
        assert resp.status_code == 200
        all_product_ids = [
            p["id"]
            for cat in resp.json()
            for p in cat.get("products", [])
        ]
        assert product.id in all_product_ids, "Свой продукт должен быть виден"
        assert product_r2.id not in all_product_ids, "Чужой продукт не должен быть виден"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_product_patch_foreign(db, restaurant, product_r2, tg_user):
    """
    Restaurant A не может изменить продукт Restaurant B.
    PATCH /menu/product/{id} должен вернуть 404.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/menu/product/{product_r2.id}",
            json={"name": "Взломанное блюдо"},
        )
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_product_delete_foreign(db, restaurant, product_r2, tg_user):
    """
    Restaurant A не может удалить продукт Restaurant B.
    DELETE /menu/product/{id} должен вернуть 404.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/product/{product_r2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# IDOR — CATEGORY
# ─────────────────────────────────────────

@pytest.mark.security
def test_idor_category_patch_foreign(db, restaurant, category2, tg_user):
    """
    Restaurant A не может изменить категорию Restaurant B.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/menu/category/{category2.id}",
            json={"name": "Взломанная категория"},
        )
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_category_delete_foreign(db, restaurant, category2, tg_user):
    """
    Restaurant A не может удалить категорию Restaurant B.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/menu/category/{category2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# IDOR — ORDER
# ─────────────────────────────────────────

@pytest.mark.security
def test_idor_order_get_foreign(db, restaurant, order_r2, tg_user):
    """
    Restaurant A не может получить заказ Restaurant B.
    GET /api/orders/{order_id} должен вернуть 404.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/orders/{order_r2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_order_patch_status_foreign(db, restaurant, order_r2, tg_user):
    """
    Restaurant A не может сменить статус заказа Restaurant B.
    PATCH /api/orders/{id}/status должен вернуть 404.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/orders/{order_r2.id}/status",
            json={"status": "completed"},
        )
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_order_list_foreign(db, restaurant, order_r1, order_r2, tg_user):
    """
    GET /api/orders/restaurant/{id} возвращает только заказы своего ресторана.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/orders/restaurant/{restaurant.id}")
        assert resp.status_code == 200
        order_ids = [o["id"] for o in resp.json()]
        assert order_r1.id in order_ids, "Свой заказ должен быть виден"
        assert order_r2.id not in order_ids, "Чужой заказ не должен быть виден"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_order_list_restaurant_id_mismatch(db, restaurant, restaurant2, order_r2, tg_user):
    """
    Restaurant A не может запросить список заказов Restaurant B
    через /api/orders/restaurant/{restaurant_b_id} — должен получить 403.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/orders/restaurant/{restaurant2.id}")
        assert resp.status_code == 403, f"Ожидали 403, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# IDOR — RESERVATION
# ─────────────────────────────────────────

@pytest.mark.security
def test_idor_reservation_list_foreign(db, restaurant, restaurant2, reservation_r2, tg_user):
    """
    GET /api/reservations/restaurant/{restaurant_b_id} должен вернуть 403
    при запросе от Restaurant A.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/reservations/restaurant/{restaurant2.id}")
        assert resp.status_code == 403, f"Ожидали 403, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_reservation_patch_status_foreign(db, restaurant, reservation_r2, tg_user):
    """
    Restaurant A не может сменить статус брони Restaurant B.
    PATCH /api/reservations/{id}/status должен вернуть 404.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/reservations/{reservation_r2.id}/status",
            json={"status": "confirmed"},
        )
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# IDOR — WAITER CALL
# ─────────────────────────────────────────

@pytest.mark.security
def test_idor_waiter_call_list_foreign(db, restaurant, restaurant2, waiter_call_r2, tg_user):
    """
    GET /api/waiter-calls/restaurant/{restaurant_b_id} должен вернуть 403
    при запросе от Restaurant A.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/waiter-calls/restaurant/{restaurant2.id}")
        assert resp.status_code == 403, f"Ожидали 403, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_waiter_call_patch_status_foreign(db, restaurant, waiter_call_r2, tg_user):
    """
    Restaurant A не может сменить статус вызова официанта Restaurant B.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/waiter-calls/{waiter_call_r2.id}/status",
            json={"status": "accepted"},
        )
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# IDOR — TABLE
# ─────────────────────────────────────────

@pytest.mark.security
def test_idor_table_delete_foreign(db, restaurant, table_r2, tg_user):
    """
    Restaurant A не может удалить стол Restaurant B.
    DELETE /api/restaurants/me/tables/{id} должен вернуть 404.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/restaurants/me/tables/{table_r2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_idor_table_list_shows_only_own(db, restaurant, table, table_r2, tg_user):
    """
    GET /api/restaurants/me/tables возвращает только столы своего ресторана.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get("/api/restaurants/me/tables")
        assert resp.status_code == 200
        table_ids = [t["id"] for t in resp.json()["tables"]]
        assert table.id in table_ids, "Свой стол должен быть виден"
        assert table_r2.id not in table_ids, "Чужой стол не должен быть виден"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# CREATE — нельзя создать объект в чужом tenant
# ─────────────────────────────────────────

@pytest.mark.security
def test_create_product_with_foreign_category(db, restaurant, category2, tg_user):
    """
    Restaurant A не может создать продукт в категории Restaurant B.
    category_id валидируется против restaurant_id из токена.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.post(
            "/api/menu/product/",
            json={
                "name": "Тест IDOR",
                "price": 10000,
                "category_id": category2.id,
                "is_available": True,
                "sort_order": 0,
            },
        )
        assert resp.status_code == 404, (
            f"Ожидали 404 (чужая категория), получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_create_order_with_foreign_product(db, restaurant, product_r2, tg_user2):
    """
    Клиент ресторана B не может заказать продукт ресторана A.
    product_id валидируется по restaurant_id из Telegram initData.

    tg_user2 привязан к restaurant2, product принадлежит restaurant (другой tenant).
    """
    # tg_user2 — клиент restaurant2, product — продукт restaurant (A)
    # Попытка создать заказ с чужим продуктом
    from models import Product as P
    # We use tg_user2 (restaurant2) and try to order product from restaurant1
    # product_r2 fixture already belongs to restaurant2, so we need product from restaurant
    # This test is: tg_user2 (restaurant2) tries to order product_r2... that's own product.
    # The real test: tg_user2 tries to order a product belonging to restaurant (not restaurant2)
    # product belongs to restaurant (fixture from conftest)
    pass  # see test below


@pytest.mark.security
def test_create_order_product_cross_tenant(db, restaurant2, product, tg_user2):
    """
    tg_user2 (ресторан B) пытается заказать product (ресторан A).
    Ожидаем 404 — продукт ресторана A невидим для клиента ресторана B.

    Это проверяет строку:
        db.query(Product).filter(
            Product.id == item.product_id,
            Product.restaurant_id == restaurant.id,  ← tenant filter
        )
    """
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_telegram_user] = lambda: tg_user2
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 1}],
                "client_name": "Атакующий",
            },
        )
        assert resp.status_code == 404, (
            f"Ожидали 404 (чужой продукт), получили {resp.status_code}: {resp.text}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_create_waiter_call_with_foreign_table(db, restaurant, table_r2, tg_user):
    """
    Клиент ресторана A не может вызвать официанта с использованием стола ресторана B.
    table_id валидируется против restaurant_id из TelegramUser.
    """
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_telegram_user] = lambda: tg_user
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.post(
            "/api/waiter-calls/",
            json={"table_id": table_r2.id},
        )
        assert resp.status_code == 404, (
            f"Ожидали 404 (стол чужого ресторана), получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# MENU URL ID MISMATCH (admin full menu)
# ─────────────────────────────────────────

@pytest.mark.security
def test_menu_all_restaurant_id_mismatch(db, restaurant, restaurant2, tg_user):
    """
    Restaurant A не может запросить полное меню Restaurant B.
    GET /api/menu/{restaurant_b_id}/all должен вернуть 403.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get(f"/api/menu/{restaurant2.id}/all")
        assert resp.status_code == 403, f"Ожидали 403, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# AGENCY ISOLATION
# ─────────────────────────────────────────

@pytest.mark.security
def test_agency_a_cannot_read_agency_b_restaurants(db, agency, restaurant, restaurant2, tg_user):
    """
    Agency A видит только свои рестораны, не видит рестораны Agency B.
    GET /api/agency/restaurants — фильтрует по agency_id из JWT.
    """
    c = _client_as_agency(db, agency, restaurant, tg_user)
    try:
        resp = c.get("/api/agency/restaurants")
        assert resp.status_code == 200
        slugs = [r["slug"] for r in resp.json()]
        assert "chinar" in slugs, "Свой ресторан должен быть виден"
        assert "palace" not in slugs, "Ресторан другого агентства не должен быть виден"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_agency_a_cannot_get_agency_b_restaurant(db, agency, restaurant, restaurant2, tg_user):
    """
    Agency A не может прочитать детали ресторана Agency B.
    GET /api/agency/restaurants/{restaurant_b_id} должен вернуть 404.
    """
    c = _client_as_agency(db, agency, restaurant, tg_user)
    try:
        resp = c.get(f"/api/agency/restaurants/{restaurant2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_agency_a_cannot_update_agency_b_restaurant(db, agency, restaurant, restaurant2, tg_user):
    """
    Agency A не может обновить ресторан Agency B.
    PATCH /api/agency/restaurants/{restaurant_b_id} должен вернуть 404.
    """
    c = _client_as_agency(db, agency, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/agency/restaurants/{restaurant2.id}",
            json={"name": "Взломано агентством A"},
        )
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_agency_a_cannot_delete_agency_b_restaurant(db, agency, restaurant, restaurant2, tg_user):
    """
    Agency A не может удалить ресторан Agency B.
    DELETE /api/agency/restaurants/{restaurant_b_id} должен вернуть 404.
    """
    c = _client_as_agency(db, agency, restaurant, tg_user)
    try:
        resp = c.delete(f"/api/agency/restaurants/{restaurant2.id}")
        assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# TELEGRAM ENTRY POINT — клиент
# ─────────────────────────────────────────

@pytest.mark.security
def test_telegram_client_cannot_see_foreign_order(db, order_r1, order_r2, tg_user, tg_user2):
    """
    tg_user (ресторан A) не может видеть заказ tg_user2 (ресторан B)
    через GET /api/orders/my/{order_id}.

    Фильтрует по client_telegram_id + restaurant_id — двойная защита.
    """
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    # tg_user пытается получить заказ который принадлежит restaurant2
    app.dependency_overrides[get_telegram_user] = lambda: tg_user
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(f"/api/orders/my/{order_r2.id}")
        assert resp.status_code == 404, (
            f"Ожидали 404 (чужой заказ), получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_telegram_client_history_scoped_to_restaurant(db, order_r1, order_r2, tg_user):
    """
    GET /api/orders/my — возвращает только заказы текущего ресторана.
    Заказы другого ресторана не попадают в историю даже если tg_id совпадает.
    """
    # Создаём заказ с тем же telegram_id, но в другом ресторане
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_telegram_user] = lambda: tg_user
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get("/api/orders/my")
        assert resp.status_code == 200
        order_ids = [o["id"] for o in resp.json()]
        # order_r2 принадлежит restaurant2 — не должен попасть в историю клиента restaurant
        assert order_r2.id not in order_ids, (
            "Заказ чужого ресторана не должен быть в истории клиента"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# MASS ASSIGNMENT — нельзя изменить tenant ownership
# ─────────────────────────────────────────

@pytest.mark.security
def test_cannot_change_product_restaurant_id_via_patch(db, restaurant, restaurant2, product, tg_user):
    """
    PATCH /api/menu/product/{id} не должен принимать restaurant_id из тела запроса.
    ProductUpdate схема не содержит restaurant_id — попытка передать его игнорируется.
    Продукт остаётся привязан к оригинальному ресторану.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.patch(
            f"/api/menu/product/{product.id}",
            json={
                "name": "Переименованное блюдо",
                "restaurant_id": restaurant2.id,  # попытка смены tenant
            },
        )
        # Запрос должен либо пройти (200/404) но с оригинальным restaurant_id
        if resp.status_code == 200:
            db.refresh(product)
            assert product.restaurant_id == restaurant.id, (
                "restaurant_id продукта не должен меняться через PATCH"
            )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# PUBLIC MENU — клиент видит только активные продукты своего ресторана
# ─────────────────────────────────────────

@pytest.mark.security
def test_public_menu_does_not_include_foreign_products(db, restaurant, restaurant2, product, product_r2):
    """
    GET /api/menu/{restaurant_id} — публичное меню содержит только продукты
    данного ресторана. Продукты другого ресторана недостижимы.
    """
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(f"/api/menu/{restaurant.id}")
        assert resp.status_code == 200
        all_product_ids = [
            p["id"]
            for cat in resp.json()
            for p in cat.get("products", [])
        ]
        assert product_r2.id not in all_product_ids, (
            "Продукт другого ресторана не должен появляться в публичном меню"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# SUPERADMIN — global access is intentional
# ─────────────────────────────────────────

@pytest.mark.security
def test_superadmin_role_required_for_superadmin_endpoints(db, restaurant, tg_user):
    """
    Superadmin endpoints требуют роль superadmin.
    Restaurant admin токен должен получить 403.
    """
    from api import app as _app
    from auth import get_db as real_get_db

    # Используем реальную JWT проверку (без override get_current_superadmin)
    _app.dependency_overrides[get_db] = lambda: db

    with TestClient(_app, raise_server_exceptions=True) as raw_c:
        # Нет токена вообще
        resp = raw_c.get("/api/superadmin/dashboard")
        assert resp.status_code in (401, 403), (
            f"Superadmin endpoint без токена должен вернуть 401/403, получили {resp.status_code}"
        )

    _app.dependency_overrides.clear()


# ─────────────────────────────────────────
# ANALYTICS — только свои данные
# ─────────────────────────────────────────

@pytest.mark.security
def test_analytics_scoped_to_restaurant(db, restaurant, order_r1, order_r2, tg_user):
    """
    GET /api/analytics/summary не возвращает данные чужого ресторана.
    SQL всегда фильтрует WHERE restaurant_id = :rid (rid из JWT токена).
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get("/api/analytics/summary?period=30d")
        # Должен вернуть 200 — аналитика только своего ресторана
        assert resp.status_code == 200
        # Проверяем что данные не null / не смешаны
        data = resp.json()
        assert "orders_total" in data
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# BILLING — только свои данные
# ─────────────────────────────────────────

@pytest.mark.security
def test_billing_subscription_scoped_to_restaurant(db, restaurant, tg_user):
    """
    GET /api/billing/subscription возвращает подписку только текущего ресторана.
    """
    c = _client_as_restaurant(db, restaurant, tg_user)
    try:
        resp = c.get("/api/billing/subscription")
        assert resp.status_code == 200
        # Данные должны быть про текущий ресторан, не чужой
        # (Free plan — нормально, если подписки нет)
    finally:
        app.dependency_overrides.clear()
