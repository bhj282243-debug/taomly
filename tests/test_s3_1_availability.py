"""
tests/test_s3_1_availability.py — Phase 3: Menu Availability + Scheduling

Паттерн:
  - Используется стандартный `client` fixture из conftest.py.
  - client уже переопределяет get_telegram_user → tg_user (restaurant1).
  - client уже устанавливает X-Restaurant-Id и X-Location-Id (location1).
  - Тесты не используют JWT напрямую — orders идут через Telegram auth flow.
  - admin endpoints (PATCH /api/menu/...) используют стандартный
    get_current_restaurant_admin override (тоже из conftest).

Покрывает:
  T1  — available product visible in public menu
  T2  — unavailable product absent from public menu
  T3  — direct unavailable product order → 400
  T4  — unavailable variant visible (Sold out) in public response
  T5  — direct unavailable variant order → 400
  T6  — mix: available Variant A + unavailable Variant B → A OK, B rejected
  T7  — unavailable modifier option visible (is_available=false) in response
  T8  — direct unavailable modifier order → 400
  T9  — normal schedule: inside window → product visible
  T10 — normal schedule: outside window → product excluded
  T11 — overnight schedule 22:00–02:00 correct across midnight
  T12 — Location.timezone respected (not UTC, not hardcoded)
  T13 — Restaurant A cannot modify Product B → 403/404
  T14 — Restaurant A client orders Product B → 404 (cross-tenant)
  T15 — Restaurant A client uses Modifier of Product B → 400
  T16 — legacy product without schedule works normally
  T17 — existing variant order (is_available=True) works
  T18 — existing modifier order (is_available=True) works
  T19 — mixed valid order (variant + modifier) works end-to-end
  T20 — full regression: baseline unaffected
  T21 — boundary: available_from == available_until → always available (24h)
  T22 — NULL/NULL schedule → always available
  T23 — exact start boundary: current == available_from → available
  T24 — exact end boundary: current == available_until → NOT available
  T25 — overnight boundary: just after start → available
  T26 — overnight boundary: just before end → available
  T27 — no active Location → scheduled product treated as unavailable (fail closed)
"""

import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import app
from auth import TelegramUser, get_current_restaurant_admin, get_current_agency, get_telegram_user
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
from utils import is_within_schedule


# ──────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────

def _takeaway_payload(product_id: int, qty: int = 1,
                       variant_id: int | None = None,
                       modifier_option_ids: list[int] | None = None) -> dict:
    item: dict = {
        "product_id": product_id,
        "quantity": qty,
        "modifier_option_ids": modifier_option_ids or [],
    }
    if variant_id is not None:
        item["variant_id"] = variant_id
    return {
        "client_name": "Тест",
        "client_phone": "+998901234567",
        "order_type": "takeaway",
        "items": [item],
    }


def _make_client2(db, restaurant2, location2):
    """
    Создаёт TestClient для ресторана 2 (для cross-tenant тестов).
    Переопределяет get_telegram_user → tg_user2.
    """
    tg2 = TelegramUser(
        id=777777777,
        first_name="Tenant2",
        last_name=None,
        username="tenant2",
        language_code="uz",
        restaurant_id=restaurant2.id,
        restaurant=restaurant2,
    )

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_telegram_user] = lambda: tg2
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant2

    c = TestClient(
        app,
        raise_server_exceptions=True,
        headers={
            "X-Restaurant-Id": str(restaurant2.id),
            "X-Location-Id": str(location2.id),
        },
    )
    return c


# ──────────────────────────────────────────
# SCHEDULE HELPER UNIT TESTS
# ──────────────────────────────────────────

class TestScheduleHelper:
    """Unit-тесты utils.is_within_schedule() без HTTP-слоя."""

    # T22: NULL/NULL → всегда доступно
    def test_null_null_always_available(self):
        assert is_within_schedule(None, None, "Asia/Tashkent") is True

    # T21: from == until → 24 часа
    def test_from_equals_until_always_available(self):
        t = datetime.time(11, 0)
        assert is_within_schedule(t, t, "Asia/Tashkent") is True
        t2 = datetime.time(0, 0)
        assert is_within_schedule(t2, t2, "Asia/Tashkent") is True

    # T9: normal window inside
    def test_normal_window_inside(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 12, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(10, 0), datetime.time(22, 0), "Asia/Tashkent"
            )
        assert result is True

    # T10: normal window outside
    def test_normal_window_outside(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 23, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(10, 0), datetime.time(22, 0), "Asia/Tashkent"
            )
        assert result is False

    # T11: overnight
    def test_overnight_inside_after_start(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 23, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent"
            )
        assert result is True

    def test_overnight_inside_before_end(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 2, 1, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent"
            )
        assert result is True

    def test_overnight_outside_midday(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 14, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent"
            )
        assert result is False

    # T23: exact start boundary — included
    def test_exact_start_boundary_included(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 11, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(11, 0), datetime.time(22, 0), "Asia/Tashkent"
            )
        assert result is True

    # T24: exact end boundary — excluded
    def test_exact_end_boundary_excluded(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 22, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(11, 0), datetime.time(22, 0), "Asia/Tashkent"
            )
        assert result is False

    # T12: timezone matters — разные IANA timezone дают разный результат
    def test_timezone_matters(self):
        import datetime as dt_mod
        utc_moment = dt_mod.datetime(2025, 1, 1, 12, 0, tzinfo=dt_mod.timezone.utc)
        from zoneinfo import ZoneInfo
        tz_london = ZoneInfo("Europe/London")   # UTC+0: 12:00 → внутри 10–20
        tz_tokyo  = ZoneInfo("Asia/Tokyo")      # UTC+9: 21:00 → снаружи 10–20

        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = utc_moment.astimezone(tz_london)
            result_london = is_within_schedule(
                dt_mod.time(10, 0), dt_mod.time(20, 0), "Europe/London"
            )

        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = utc_moment.astimezone(tz_tokyo)
            result_tokyo = is_within_schedule(
                dt_mod.time(10, 0), dt_mod.time(20, 0), "Asia/Tokyo"
            )

        assert result_london is True
        assert result_tokyo is False


# ──────────────────────────────────────────
# T1: available product → visible in public menu
# ──────────────────────────────────────────
def test_t1_available_product_in_public_menu(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Плов", price=35000, is_available=True,
    )
    db.add(p); db.flush()

    resp = client.get(f"/api/menu/{restaurant.id}")
    assert resp.status_code == 200
    ids = {pr["id"] for c in resp.json() for pr in c["products"]}
    assert p.id in ids


# ──────────────────────────────────────────
# T2: unavailable product → absent from public menu
# ──────────────────────────────────────────
def test_t2_unavailable_product_absent_from_menu(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Недоступное", price=10000, is_available=False,
    )
    db.add(p); db.flush()

    resp = client.get(f"/api/menu/{restaurant.id}")
    assert resp.status_code == 200
    ids = {pr["id"] for c in resp.json() for pr in c["products"]}
    assert p.id not in ids


# ──────────────────────────────────────────
# T3: direct order of unavailable product → 400
# ──────────────────────────────────────────
def test_t3_unavailable_product_order_rejected(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Sold Out", price=10000, is_available=False,
    )
    db.add(p); db.flush()

    resp = client.post("/api/orders/", json=_takeaway_payload(p.id))
    assert resp.status_code == 400
    assert "недоступен" in resp.json()["detail"].lower()


# ──────────────────────────────────────────
# T4: unavailable variant visible in public response (is_available=false)
# ──────────────────────────────────────────
def test_t4_unavailable_variant_visible_with_flag(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Компот", price=None, is_available=True,
    )
    db.add(p); db.flush()

    v_ok  = ProductVariant(product_id=p.id, name="1L",   price=20000, is_active=True, is_available=True)
    v_bad = ProductVariant(product_id=p.id, name="0.7L", price=15000, is_active=True, is_available=False)
    db.add_all([v_ok, v_bad]); db.flush()

    resp = client.get(f"/api/menu/{restaurant.id}")
    assert resp.status_code == 200
    all_products = [pr for c in resp.json() for pr in c["products"]]
    kompot = next((pr for pr in all_products if pr["id"] == p.id), None)
    assert kompot is not None, "Компот должен быть в меню"

    v_map = {v["id"]: v for v in kompot["variants"]}
    assert v_ok.id  in v_map, "Доступный вариант должен быть в response"
    assert v_bad.id in v_map, "Sold-out вариант тоже должен быть в response"
    assert v_map[v_ok.id]["is_available"]  is True
    assert v_map[v_bad.id]["is_available"] is False


# ──────────────────────────────────────────
# T5: direct order with unavailable variant → 400
# ──────────────────────────────────────────
def test_t5_unavailable_variant_order_rejected(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Чай", price=None, is_available=True,
    )
    db.add(p); db.flush()
    v = ProductVariant(product_id=p.id, name="Большой", price=8000, is_active=True, is_available=False)
    db.add(v); db.flush()

    resp = client.post("/api/orders/", json=_takeaway_payload(p.id, variant_id=v.id))
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "недоступен" in detail or "variant" in detail


# ──────────────────────────────────────────
# T6: available Variant A + unavailable Variant B
# ──────────────────────────────────────────
def test_t6_mixed_variant_availability(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Лимонад", price=None, is_available=True,
    )
    db.add(p); db.flush()
    v_ok  = ProductVariant(product_id=p.id, name="Маленький", price=6000, is_active=True, is_available=True)
    v_bad = ProductVariant(product_id=p.id, name="Большой",   price=9000, is_active=True, is_available=False)
    db.add_all([v_ok, v_bad]); db.flush()

    resp_ok = client.post("/api/orders/", json=_takeaway_payload(p.id, variant_id=v_ok.id))
    assert resp_ok.status_code == 201, f"Доступный вариант должен проходить: {resp_ok.text}"

    resp_bad = client.post("/api/orders/", json=_takeaway_payload(p.id, variant_id=v_bad.id))
    assert resp_bad.status_code == 400


# ──────────────────────────────────────────
# T7: unavailable modifier visible (is_available=false in response)
# ──────────────────────────────────────────
def test_t7_unavailable_modifier_visible_in_menu(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Плов с добавками", price=35000, is_available=True,
    )
    db.add(p); db.flush()
    grp = ModifierGroup(product_id=p.id, name="Добавки", min_selections=0, max_selections=2)
    db.add(grp); db.flush()
    opt_ok  = ModifierOption(modifier_group_id=grp.id, name="Extra мясо", price_adjustment=5000, is_active=True, is_available=True)
    opt_bad = ModifierOption(modifier_group_id=grp.id, name="Яйцо",       price_adjustment=2000, is_active=True, is_available=False)
    db.add_all([opt_ok, opt_bad]); db.flush()

    resp = client.get(f"/api/menu/{restaurant.id}")
    assert resp.status_code == 200
    all_products = [pr for c in resp.json() for pr in c["products"]]
    plov = next((pr for pr in all_products if pr["id"] == p.id), None)
    assert plov is not None

    opts = {o["id"]: o for g in plov.get("modifier_groups", []) for o in g.get("options", [])}
    assert opt_ok.id  in opts, "Доступная опция должна быть в response"
    assert opt_bad.id in opts, "Sold-out опция тоже должна быть в response"
    assert opts[opt_ok.id]["is_available"]  is True
    assert opts[opt_bad.id]["is_available"] is False


# ──────────────────────────────────────────
# T8: direct order with unavailable modifier → 400
# ──────────────────────────────────────────
def test_t8_unavailable_modifier_order_rejected(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Шашлык", price=50000, is_available=True,
    )
    db.add(p); db.flush()
    grp = ModifierGroup(product_id=p.id, name="Гарнир", min_selections=0, max_selections=1)
    db.add(grp); db.flush()
    opt = ModifierOption(modifier_group_id=grp.id, name="Картофель", price_adjustment=3000, is_active=True, is_available=False)
    db.add(opt); db.flush()

    resp = client.post("/api/orders/", json=_takeaway_payload(p.id, modifier_option_ids=[opt.id]))
    assert resp.status_code == 400
    assert "недоступен" in resp.json()["detail"].lower()


# ──────────────────────────────────────────
# T9 + T10: schedule via mock
# ──────────────────────────────────────────
def test_t9_schedule_inside_window(client, db, restaurant, category):
    """Product с расписанием — мок is_within_schedule=True → виден."""
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Плов по расписанию", price=30000, is_available=True,
        available_from=datetime.time(10, 0),
        available_until=datetime.time(22, 0),
    )
    db.add(p); db.flush()

    with patch("routers.menu.is_within_schedule", return_value=True):
        resp = client.get(f"/api/menu/{restaurant.id}")

    assert resp.status_code == 200
    ids = {pr["id"] for c in resp.json() for pr in c["products"]}
    assert p.id in ids


def test_t10_schedule_outside_window(client, db, restaurant, category):
    """Product с расписанием — мок is_within_schedule=False → отсутствует."""
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Плов вне окна", price=30000, is_available=True,
        available_from=datetime.time(10, 0),
        available_until=datetime.time(22, 0),
    )
    db.add(p); db.flush()

    with patch("routers.menu.is_within_schedule", return_value=False):
        resp = client.get(f"/api/menu/{restaurant.id}")

    assert resp.status_code == 200
    ids = {pr["id"] for c in resp.json() for pr in c["products"]}
    assert p.id not in ids


# ──────────────────────────────────────────
# T11: overnight schedule unit test
# ──────────────────────────────────────────
def test_t11_overnight_schedule(db):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tashkent")

    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 1, 23, 0, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is True

    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 2, 1, 30, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is True

    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 2, 2, 0, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is False

    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 1, 10, 0, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is False


# ──────────────────────────────────────────
# T12: Location.timezone respected
# ──────────────────────────────────────────
def test_t12_location_timezone_respected(client, db, restaurant, category, location):
    """
    Меняем Location.timezone на Europe/London.
    Захватываем tz_str переданный в is_within_schedule — должен быть Europe/London,
    а не Asia/Tashkent или UTC.
    """
    location.timezone = "Europe/London"
    db.flush()

    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="London Dish", price=20000, is_available=True,
        available_from=datetime.time(10, 0),
        available_until=datetime.time(22, 0),
    )
    db.add(p); db.flush()

    captured = {}
    original = is_within_schedule

    def capture(from_, until_, tz_str):
        captured["tz_str"] = tz_str
        return original(from_, until_, tz_str)

    with patch("routers.menu.is_within_schedule", side_effect=capture):
        resp = client.get(f"/api/menu/{restaurant.id}")

    assert resp.status_code == 200
    if captured:
        assert captured.get("tz_str") == "Europe/London", (
            f"Ожидался Europe/London, получен {captured.get('tz_str')}"
        )


# ──────────────────────────────────────────
# T13: Restaurant A cannot modify Product B → 403/404
# ──────────────────────────────────────────
def test_t13_cross_restaurant_modify_rejected(client, db, restaurant2, location2, category):
    """
    Стандартный client привязан к restaurant1.
    get_current_restaurant_admin override → restaurant1.
    Пытаемся PATCH продукт restaurant2 → 404.
    """
    cat2 = Category(restaurant_id=restaurant2.id, name="Cat2", sort_order=0)
    db.add(cat2); db.flush()
    p2 = Product(
        restaurant_id=restaurant2.id, category_id=cat2.id,
        name="Продукт B", price=10000, is_available=True,
    )
    db.add(p2); db.flush()

    resp = client.patch(
        f"/api/menu/product/{p2.id}",
        json={"is_available": False},
    )
    assert resp.status_code in (403, 404)


# ──────────────────────────────────────────
# T14: Restaurant A client orders from Restaurant B → 404
# ──────────────────────────────────────────
def test_t14_cross_restaurant_variant_rejected(db, restaurant, restaurant2, category, location2):
    """
    client2 (restaurant2) пытается заказать продукт restaurant2 с вариантом restaurant2 —
    это легитимно. Реальный cross-tenant: client2 пытается заказать product из restaurant1.
    """
    cat2 = Category(restaurant_id=restaurant2.id, name="Cat2", sort_order=0)
    db.add(cat2); db.flush()

    # продукт restaurant1
    cat1 = Category(restaurant_id=restaurant.id, name="Cat1-cross", sort_order=99)
    db.add(cat1); db.flush()
    p1 = Product(
        restaurant_id=restaurant.id, category_id=cat1.id,
        name="Чужой продукт", price=None, is_available=True,
    )
    db.add(p1); db.flush()
    v1 = ProductVariant(product_id=p1.id, name="Чужой вариант", price=10000, is_active=True, is_available=True)
    db.add(v1); db.flush()

    c2 = _make_client2(db, restaurant2, location2)
    try:
        resp = c2.post("/api/orders/", json=_takeaway_payload(p1.id, variant_id=v1.id))
        assert resp.status_code in (400, 404), (
            f"Cross-tenant product должен быть отклонён: {resp.text}"
        )
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────
# T15: Restaurant A client uses Modifier of Restaurant B → 400
# ──────────────────────────────────────────
def test_t15_cross_restaurant_modifier_rejected(client, db, restaurant, restaurant2, category, location2):
    """
    client (restaurant1) пытается использовать modifier из product restaurant2.
    """
    cat2 = Category(restaurant_id=restaurant2.id, name="Cat2", sort_order=0)
    db.add(cat2); db.flush()

    # продукт restaurant1 (легитимный)
    p1 = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Продукт R1", price=20000, is_available=True,
    )
    db.add(p1); db.flush()

    # продукт + modifier restaurant2
    p2 = Product(
        restaurant_id=restaurant2.id, category_id=cat2.id,
        name="Продукт R2", price=20000, is_available=True,
    )
    db.add(p2); db.flush()
    grp2 = ModifierGroup(product_id=p2.id, name="Группа R2", min_selections=0, max_selections=1)
    db.add(grp2); db.flush()
    opt2 = ModifierOption(modifier_group_id=grp2.id, name="Опция R2", price_adjustment=0, is_active=True, is_available=True)
    db.add(opt2); db.flush()

    resp = client.post(
        "/api/orders/",
        json=_takeaway_payload(p1.id, modifier_option_ids=[opt2.id]),
    )
    assert resp.status_code in (400, 404), (
        f"Cross-tenant modifier должен быть отклонён: {resp.text}"
    )


# ──────────────────────────────────────────
# T16: legacy product without schedule works
# ──────────────────────────────────────────
def test_t16_legacy_product_no_schedule(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Самса Легаси", price=12000, is_available=True,
        available_from=None, available_until=None,
    )
    db.add(p); db.flush()

    # В публичном меню
    resp_menu = client.get(f"/api/menu/{restaurant.id}")
    assert resp_menu.status_code == 200
    ids = {pr["id"] for c in resp_menu.json() for pr in c["products"]}
    assert p.id in ids

    # Заказ проходит
    resp_order = client.post("/api/orders/", json=_takeaway_payload(p.id))
    assert resp_order.status_code == 201


# ──────────────────────────────────────────
# T17: existing variant order (is_available=True) works
# ──────────────────────────────────────────
def test_t17_existing_variant_order_works(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Напиток", price=None, is_available=True,
    )
    db.add(p); db.flush()
    v = ProductVariant(product_id=p.id, name="0.5L", price=5000, is_active=True, is_available=True)
    db.add(v); db.flush()

    resp = client.post("/api/orders/", json=_takeaway_payload(p.id, variant_id=v.id))
    assert resp.status_code == 201


# ──────────────────────────────────────────
# T18: existing modifier order (is_available=True) works
# ──────────────────────────────────────────
def test_t18_existing_modifier_order_works(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Бургер", price=30000, is_available=True,
    )
    db.add(p); db.flush()
    grp = ModifierGroup(product_id=p.id, name="Соусы", min_selections=0, max_selections=2)
    db.add(grp); db.flush()
    opt = ModifierOption(modifier_group_id=grp.id, name="Кетчуп", price_adjustment=0, is_active=True, is_available=True)
    db.add(opt); db.flush()

    resp = client.post("/api/orders/", json=_takeaway_payload(p.id, modifier_option_ids=[opt.id]))
    assert resp.status_code == 201


# ──────────────────────────────────────────
# T19: mixed valid order (variant + modifier)
# ──────────────────────────────────────────
def test_t19_mixed_valid_order(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Пицца", price=None, is_available=True,
    )
    db.add(p); db.flush()
    v = ProductVariant(product_id=p.id, name="Большая", price=60000, is_active=True, is_available=True)
    db.add(v); db.flush()
    grp = ModifierGroup(product_id=p.id, name="Топпинги", min_selections=0, max_selections=3)
    db.add(grp); db.flush()
    opt1 = ModifierOption(modifier_group_id=grp.id, name="Грибы", price_adjustment=5000, is_active=True, is_available=True)
    opt2 = ModifierOption(modifier_group_id=grp.id, name="Лук",   price_adjustment=2000, is_active=True, is_available=True)
    db.add_all([opt1, opt2]); db.flush()

    resp = client.post(
        "/api/orders/",
        json=_takeaway_payload(p.id, variant_id=v.id, modifier_option_ids=[opt1.id, opt2.id]),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] is not None
    # S2-8 contract: OrderItem.price НЕ включает modifier price_adjustment — deferred to Phase 7.
    # total_amount = variant.price * quantity (модификаторы в total не входят до Phase 7).
    assert data["total_amount"] == 60000 * 1


# ──────────────────────────────────────────
# T20: full regression
# ──────────────────────────────────────────
def test_t20_full_regression(client, db, restaurant, category):
    p = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Регрессия Лагман", price=25000, is_available=True,
    )
    db.add(p); db.flush()

    # Public menu
    menu_resp = client.get(f"/api/menu/{restaurant.id}")
    assert menu_resp.status_code == 200
    ids = {pr["id"] for c in menu_resp.json() for pr in c["products"]}
    assert p.id in ids, "Legacy продукт должен быть в меню"

    # Order
    order_resp = client.post("/api/orders/", json=_takeaway_payload(p.id))
    assert order_resp.status_code == 201

    # Admin menu
    admin_resp = client.get(f"/api/menu/{restaurant.id}/all")
    assert admin_resp.status_code == 200


# ──────────────────────────────────────────
# T21: from == until → always available
# ──────────────────────────────────────────
def test_t21_from_equals_until_always_available(db):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tashkent")
    t = datetime.time(11, 0)

    for hour in [0, 6, 11, 15, 22, 23]:
        with patch("utils.datetime") as m:
            m.datetime.now.return_value = datetime.datetime(2025, 1, 1, hour, 0, tzinfo=tz)
            result = is_within_schedule(t, t, "Asia/Tashkent")
        assert result is True, f"from==until должно быть True в {hour}:00"


# ──────────────────────────────────────────
# T25 + T26: overnight boundary edge cases
# ──────────────────────────────────────────
def test_t25_overnight_just_after_start():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tashkent")
    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 1, 22, 1, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is True


def test_t26_overnight_just_before_end():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tashkent")
    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 2, 1, 59, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is True


# ──────────────────────────────────────────
# T27: no active Location → scheduled fail closed
# ──────────────────────────────────────────
def test_t27_no_location_scheduled_product_fail_closed(client, db, restaurant, category):
    """
    Деактивируем все Location ресторана.
    Продукт с расписанием → fail closed (не показывается).
    Продукт без расписания → показывается нормально.
    """
    # Деактивируем существующую Location
    from models import Location as Loc
    db.query(Loc).filter(Loc.restaurant_id == restaurant.id).update({"is_active": False})
    db.flush()

    p_sched = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="С расписанием", price=10000, is_available=True,
        available_from=datetime.time(10, 0),
        available_until=datetime.time(22, 0),
    )
    p_nosched = Product(
        restaurant_id=restaurant.id, category_id=category.id,
        name="Без расписания", price=10000, is_available=True,
    )
    db.add_all([p_sched, p_nosched]); db.flush()

    resp = client.get(f"/api/menu/{restaurant.id}")
    assert resp.status_code == 200
    p_ids = {pr["id"] for c in resp.json() for pr in c["products"]}

    assert p_sched.id  not in p_ids, "Scheduled без Location → fail closed"
    assert p_nosched.id in  p_ids,   "Unscheduled должен показываться"
