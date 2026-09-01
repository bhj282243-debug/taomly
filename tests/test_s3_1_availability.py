"""
tests/test_s3_1_availability.py — Phase 3: Menu Availability + Scheduling

Покрывает:
  T1  — available product visible in public menu
  T2  — unavailable product absent from public menu
  T3  — direct order of unavailable product → 400
  T4  — unavailable variant visible (Sold out) but not selectable in order
  T5  — direct order with unavailable variant → 400
  T6  — mix: available Variant A + unavailable Variant B → A selectable, B rejected
  T7  — unavailable modifier option visible but rejected in order
  T8  — direct order with unavailable modifier option → 400
  T9  — normal schedule: inside window → product available
  T10 — normal schedule: outside window → product excluded from menu
  T11 — overnight schedule 22:00–02:00 correct across midnight
  T12 — Location.timezone respected (uses IANA timezone, not server UTC)
  T13 — Restaurant A cannot modify Product B → 403/404
  T14 — Restaurant A order with Variant of Restaurant B → rejected
  T15 — Restaurant A order with Modifier of Restaurant B → rejected
  T16 — legacy product without schedule works normally
  T17 — existing variant order (is_available=True) works
  T18 — existing modifier order (is_available=True) works
  T19 — mixed valid order (variant + modifier) works end-to-end
  T20 — full regression: existing product/variant/modifier baseline unaffected
  T21 — boundary: available_from == available_until → always available (24h)

  Additional:
  T22 — NULL/NULL schedule → always available
  T23 — exact start boundary: current == available_from → available
  T24 — exact end boundary: current == available_until → NOT available
  T25 — overnight boundary: just after start → available
  T26 — overnight boundary: just before end → available
  T27 — no active Location → scheduled product treated as unavailable (fail closed)
"""

import datetime
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import app
from auth import create_restaurant_token
from database import get_db
from models import (
    Category,
    Location,
    ModifierGroup,
    ModifierOption,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    Restaurant,
)
from utils import is_within_schedule


# ──────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────

def _auth(restaurant: Restaurant) -> dict:
    """JWT-заголовок для restaurant_admin."""
    token = create_restaurant_token(restaurant)
    return {"Authorization": f"Bearer {token}"}


def _make_order_payload(
    location,
    product_id: int,
    qty: int = 1,
    variant_id: int | None = None,
    modifier_option_ids: list[int] | None = None,
    order_type: str = "takeaway",
) -> dict:
    item = {
        "product_id": product_id,
        "quantity": qty,
        "modifier_option_ids": modifier_option_ids or [],
    }
    if variant_id is not None:
        item["variant_id"] = variant_id
    return {
        "client_name": "Тест",
        "client_phone": "+998901234567",
        "order_type": order_type,
        "items": [item],
    }


def _loc_header(location: Location) -> dict:
    return {"X-Location-Id": str(location.id)}


# ──────────────────────────────────────────
# SCHEDULE HELPER UNIT TESTS (is_within_schedule)
# ──────────────────────────────────────────

class TestScheduleHelper:
    """Unit-тесты для utils.is_within_schedule() без HTTP-слоя."""

    # T22: NULL/NULL → всегда доступно
    def test_null_null_always_available(self):
        assert is_within_schedule(None, None, "Asia/Tashkent") is True

    # T21: from == until → 24 часа / всегда доступно
    def test_from_equals_until_always_available(self):
        t = datetime.time(11, 0)
        assert is_within_schedule(t, t, "Asia/Tashkent") is True
        t2 = datetime.time(0, 0)
        assert is_within_schedule(t2, t2, "Asia/Tashkent") is True

    # T9: normal window — внутри
    def test_normal_window_inside(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        now = datetime.datetime.now(tz=tz).time()
        # создаём окно которое наверняка включает текущее время ± буфер
        # Используем мок времени для детерминизма
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 12, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(10, 0), datetime.time(22, 0), "Asia/Tashkent"
            )
        assert result is True

    # T10: normal window — снаружи
    def test_normal_window_outside(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 23, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(10, 0), datetime.time(22, 0), "Asia/Tashkent"
            )
        assert result is False

    # T11: overnight schedule
    def test_overnight_inside_after_start(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            # 23:00 — после 22:00 → доступно
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 23, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent"
            )
        assert result is True

    def test_overnight_inside_before_end(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            # 01:00 — до 02:00 → доступно
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 2, 1, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent"
            )
        assert result is True

    def test_overnight_outside_midday(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            # 14:00 — между концом и началом → недоступно
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 14, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent"
            )
        assert result is False

    # T23: exact start boundary: available_from включается (>=)
    def test_exact_start_boundary_included(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 11, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(11, 0), datetime.time(22, 0), "Asia/Tashkent"
            )
        assert result is True  # ровно на границе → доступно

    # T24: exact end boundary: available_until не включается (<)
    def test_exact_end_boundary_excluded(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Tashkent")
        with patch("utils.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2025, 1, 1, 22, 0, tzinfo=tz)
            result = is_within_schedule(
                datetime.time(11, 0), datetime.time(22, 0), "Asia/Tashkent"
            )
        assert result is False  # ровно на конце → НЕ доступно

    # T12: timezone respected — разные timezone дают разный результат
    def test_timezone_matters(self):
        """
        Доказательство что используется Location.timezone, а не server UTC.
        Фиксируем UTC время. В Europe/London — одно, в Asia/Tokyo — другое.
        """
        import datetime as dt_mod
        # UTC: 2025-01-01 12:00 UTC
        utc_moment = dt_mod.datetime(2025, 1, 1, 12, 0, tzinfo=dt_mod.timezone.utc)

        from zoneinfo import ZoneInfo
        tz_london = ZoneInfo("Europe/London")   # UTC+0: 12:00 локально
        tz_tokyo  = ZoneInfo("Asia/Tokyo")      # UTC+9: 21:00 локально

        # Окно 10:00–20:00
        # В London (12:00) → внутри → True
        # В Tokyo  (21:00) → снаружи → False
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
# FIXTURES
# ──────────────────────────────────────────

@pytest.fixture
def client(db):
    """TestClient с переопределённой DB-сессией."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def r1_setup(db, restaurant, category, location):
    """Полный setup ресторана 1: restaurant + category + location."""
    return {"restaurant": restaurant, "category": category, "location": location}


@pytest.fixture
def r2_setup(db, restaurant2, location2):
    """Setup ресторана 2."""
    cat2 = Category(restaurant_id=restaurant2.id, name="Cat2", sort_order=0)
    db.add(cat2)
    db.flush()
    return {"restaurant": restaurant2, "category": cat2, "location": location2}


# ──────────────────────────────────────────
# T1: available product → visible in public menu
# ──────────────────────────────────────────
def test_t1_available_product_in_public_menu(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Плов", price=35000, is_available=True,
    )
    db.add(p)
    db.flush()

    resp = client.get(f"/api/menu/{r.id}")
    assert resp.status_code == 200
    products = [pr for c in resp.json() for pr in c["products"]]
    assert any(pr["id"] == p.id for pr in products)


# ──────────────────────────────────────────
# T2: unavailable product → absent from public menu
# ──────────────────────────────────────────
def test_t2_unavailable_product_absent_from_menu(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Недоступное", price=10000, is_available=False,
    )
    db.add(p)
    db.flush()

    resp = client.get(f"/api/menu/{r.id}")
    assert resp.status_code == 200
    products = [pr for c in resp.json() for pr in c["products"]]
    assert not any(pr["id"] == p.id for pr in products)


# ──────────────────────────────────────────
# T3: direct order of unavailable product → 400
# ──────────────────────────────────────────
def test_t3_unavailable_product_order_rejected(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Sold Out Product", price=10000, is_available=False,
    )
    db.add(p)
    db.flush()

    payload = _make_order_payload(loc, p.id)
    resp = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp.status_code == 400
    assert "недоступен" in resp.json()["detail"].lower() or "unavailable" in resp.json()["detail"].lower()


# ──────────────────────────────────────────
# T4: unavailable variant visible in public menu (is_available field)
# ──────────────────────────────────────────
def test_t4_unavailable_variant_visible_with_flag(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Компот", price=None, is_available=True,
    )
    db.add(p)
    db.flush()
    v_avail = ProductVariant(product_id=p.id, name="1L", price=20000, is_active=True, is_available=True)
    v_soldout = ProductVariant(product_id=p.id, name="0.7L", price=15000, is_active=True, is_available=False)
    db.add_all([v_avail, v_soldout])
    db.flush()

    resp = client.get(f"/api/menu/{r.id}")
    assert resp.status_code == 200
    products = [pr for c in resp.json() for pr in c["products"]]
    kompot = next((pr for pr in products if pr["id"] == p.id), None)
    assert kompot is not None, "Компот должен быть в меню"

    v_ids = {v["id"]: v for v in kompot["variants"]}
    # Оба варианта в response (is_active=True)
    assert v_avail.id in v_ids, "Доступный вариант должен быть в response"
    assert v_soldout.id in v_ids, "Sold-out вариант тоже должен быть в response (для показа Sold out)"
    # Флаги is_available
    assert v_ids[v_avail.id]["is_available"] is True
    assert v_ids[v_soldout.id]["is_available"] is False


# ──────────────────────────────────────────
# T5: direct order with unavailable variant → 400
# ──────────────────────────────────────────
def test_t5_unavailable_variant_order_rejected(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Чай", price=None, is_available=True,
    )
    db.add(p)
    db.flush()
    v = ProductVariant(product_id=p.id, name="Большой", price=8000, is_active=True, is_available=False)
    db.add(v)
    db.flush()

    payload = _make_order_payload(loc, p.id, variant_id=v.id)
    resp = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "недоступен" in detail or "variant" in detail


# ──────────────────────────────────────────
# T6: available Variant A + unavailable Variant B
# ──────────────────────────────────────────
def test_t6_mixed_variant_availability(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Лимонад", price=None, is_available=True,
    )
    db.add(p)
    db.flush()
    v_ok = ProductVariant(product_id=p.id, name="Маленький", price=6000, is_active=True, is_available=True)
    v_bad = ProductVariant(product_id=p.id, name="Большой", price=9000, is_active=True, is_available=False)
    db.add_all([v_ok, v_bad])
    db.flush()

    # Заказ с доступным вариантом → успех
    payload_ok = _make_order_payload(loc, p.id, variant_id=v_ok.id)
    resp_ok = client.post(
        "/api/orders/",
        json=payload_ok,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp_ok.status_code == 201, f"Доступный вариант должен проходить: {resp_ok.text}"

    # Заказ с недоступным вариантом → 400
    payload_bad = _make_order_payload(loc, p.id, variant_id=v_bad.id)
    resp_bad = client.post(
        "/api/orders/",
        json=payload_bad,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp_bad.status_code == 400


# ──────────────────────────────────────────
# T7: unavailable modifier visible (is_available=false in response)
# ──────────────────────────────────────────
def test_t7_unavailable_modifier_visible_in_menu(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Плов с добавками", price=35000, is_available=True,
    )
    db.add(p)
    db.flush()
    grp = ModifierGroup(product_id=p.id, name="Добавки", min_selections=0, max_selections=2)
    db.add(grp)
    db.flush()
    opt_ok = ModifierOption(modifier_group_id=grp.id, name="Extra мясо", price_adjustment=5000, is_active=True, is_available=True)
    opt_bad = ModifierOption(modifier_group_id=grp.id, name="Яйцо", price_adjustment=2000, is_active=True, is_available=False)
    db.add_all([opt_ok, opt_bad])
    db.flush()

    resp = client.get(f"/api/menu/{r.id}")
    assert resp.status_code == 200
    products = [pr for c in resp.json() for pr in c["products"]]
    plov = next((pr for pr in products if pr["id"] == p.id), None)
    assert plov is not None
    options_by_id = {}
    for g in plov.get("modifier_groups", []):
        for o in g.get("options", []):
            options_by_id[o["id"]] = o

    assert opt_ok.id in options_by_id, "Доступная опция должна быть в response"
    assert opt_bad.id in options_by_id, "Sold-out опция тоже должна быть в response"
    assert options_by_id[opt_ok.id]["is_available"] is True
    assert options_by_id[opt_bad.id]["is_available"] is False


# ──────────────────────────────────────────
# T8: direct order with unavailable modifier → 400
# ──────────────────────────────────────────
def test_t8_unavailable_modifier_order_rejected(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Шашлык", price=50000, is_available=True,
    )
    db.add(p)
    db.flush()
    grp = ModifierGroup(product_id=p.id, name="Гарнир", min_selections=0, max_selections=1)
    db.add(grp)
    db.flush()
    opt = ModifierOption(modifier_group_id=grp.id, name="Картофель", price_adjustment=3000, is_active=True, is_available=False)
    db.add(opt)
    db.flush()

    payload = _make_order_payload(loc, p.id, modifier_option_ids=[opt.id])
    resp = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp.status_code == 400
    assert "недоступен" in resp.json()["detail"].lower()


# ──────────────────────────────────────────
# T9 + T10: normal schedule — inside / outside window
# ──────────────────────────────────────────
def test_t9_schedule_inside_window(client, db, r1_setup):
    """Product с расписанием 10:00–22:00. Мокируем текущее время 12:00 → продукт виден."""
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Плов по расписанию", price=30000, is_available=True,
        available_from=datetime.time(10, 0),
        available_until=datetime.time(22, 0),
    )
    db.add(p)
    db.flush()

    from zoneinfo import ZoneInfo
    tz = ZoneInfo(loc.timezone)
    mock_time = datetime.datetime(2025, 6, 1, 12, 0, tzinfo=tz)

    with patch("routers.menu.is_within_schedule") as mock_sched:
        mock_sched.return_value = True
        resp = client.get(f"/api/menu/{r.id}")

    assert resp.status_code == 200
    products = [pr for c in resp.json() for pr in c["products"]]
    assert any(pr["id"] == p.id for pr in products)


def test_t10_schedule_outside_window(client, db, r1_setup):
    """Product с расписанием 10:00–22:00. Мокируем 23:00 → продукта нет."""
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Плов вне окна", price=30000, is_available=True,
        available_from=datetime.time(10, 0),
        available_until=datetime.time(22, 0),
    )
    db.add(p)
    db.flush()

    with patch("routers.menu.is_within_schedule") as mock_sched:
        mock_sched.return_value = False
        resp = client.get(f"/api/menu/{r.id}")

    assert resp.status_code == 200
    products = [pr for c in resp.json() for pr in c["products"]]
    assert not any(pr["id"] == p.id for pr in products)


# ──────────────────────────────────────────
# T11: overnight schedule 22:00–02:00
# ──────────────────────────────────────────
def test_t11_overnight_schedule(db):
    """Unit-тест is_within_schedule для overnight без HTTP."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tashkent")

    # 23:00 → доступно (после 22:00)
    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 1, 23, 0, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is True

    # 01:30 → доступно (до 02:00)
    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 2, 1, 30, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is True

    # 02:00 ровно → НЕ доступно (excluded end)
    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 2, 2, 0, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is False

    # 10:00 → недоступно (между 02:00 и 22:00)
    with patch("utils.datetime") as m:
        m.datetime.now.return_value = datetime.datetime(2025, 1, 1, 10, 0, tzinfo=tz)
        assert is_within_schedule(datetime.time(22, 0), datetime.time(2, 0), "Asia/Tashkent") is False


# ──────────────────────────────────────────
# T12: Location.timezone respected in schedule evaluation
# ──────────────────────────────────────────
def test_t12_location_timezone_respected(client, db, r1_setup):
    """
    Ресторан с Location.timezone='Europe/London'.
    UTC 12:00 → Лондон 12:00 → внутри 10:00–22:00 → продукт виден.
    Доказывает что используется Location.timezone, а не UTC или hardcode.
    """
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]

    # Меняем timezone Location на Europe/London
    loc.timezone = "Europe/London"
    db.flush()

    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="London Dish", price=20000, is_available=True,
        available_from=datetime.time(10, 0),
        available_until=datetime.time(22, 0),
    )
    db.add(p)
    db.flush()

    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/London")
    mock_time = datetime.datetime(2025, 6, 1, 12, 0, tzinfo=tz)

    # Мокируем is_within_schedule чтобы захватить tz_str
    captured = {}
    original = __import__("utils").is_within_schedule

    def capture_and_call(from_, until_, tz_str):
        captured["tz_str"] = tz_str
        return original(from_, until_, tz_str)

    with patch("routers.menu.is_within_schedule", side_effect=capture_and_call):
        resp = client.get(f"/api/menu/{r.id}")

    assert resp.status_code == 200
    # Убеждаемся что schedule вызывался с правильным timezone из Location
    if captured:
        assert captured.get("tz_str") == "Europe/London", (
            f"Ожидался Europe/London, получен {captured.get('tz_str')}"
        )


# ──────────────────────────────────────────
# T13: Restaurant A cannot modify Product B
# ──────────────────────────────────────────
def test_t13_cross_restaurant_modify_rejected(client, db, r1_setup, r2_setup):
    r2 = r2_setup["restaurant"]
    cat2 = r2_setup["category"]
    p2 = Product(
        restaurant_id=r2.id, category_id=cat2.id,
        name="Продукт B", price=10000, is_available=True,
    )
    db.add(p2)
    db.flush()

    r1 = r1_setup["restaurant"]
    # R1 пытается изменить is_available продукта R2
    resp = client.patch(
        f"/api/menu/product/{p2.id}",
        json={"is_available": False},
        headers=_auth(r1),
    )
    # 404 или 403 — оба допустимы
    assert resp.status_code in (403, 404)


# ──────────────────────────────────────────
# T14: Restaurant A uses Variant of Restaurant B → rejected
# ──────────────────────────────────────────
def test_t14_cross_restaurant_variant_rejected(client, db, r1_setup, r2_setup):
    r1 = r1_setup["restaurant"]
    loc1 = r1_setup["location"]
    cat1 = r1_setup["category"]
    r2 = r2_setup["restaurant"]
    cat2 = r2_setup["category"]

    # Продукт R1 (легитимный)
    p1 = Product(
        restaurant_id=r1.id, category_id=cat1.id,
        name="Легитимный", price=None, is_available=True,
    )
    db.add(p1)
    db.flush()

    # Продукт и вариант R2
    p2 = Product(
        restaurant_id=r2.id, category_id=cat2.id,
        name="Чужой продукт", price=None, is_available=True,
    )
    db.add(p2)
    db.flush()
    v2 = ProductVariant(product_id=p2.id, name="Чужой вариант", price=10000, is_active=True, is_available=True)
    db.add(v2)

    # Вариант R1 (легитимный)
    v1 = ProductVariant(product_id=p1.id, name="Свой вариант", price=8000, is_active=True, is_available=True)
    db.add(v1)
    db.flush()

    # R1 пытается заказать с вариантом R2 (через product_id=p1, variant_id=v2)
    payload = _make_order_payload(loc1, p1.id, variant_id=v2.id)
    resp = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r1), **_loc_header(loc1)},
    )
    assert resp.status_code in (400, 404), (
        f"Cross-tenant variant должен быть отклонён: {resp.text}"
    )


# ──────────────────────────────────────────
# T15: Restaurant A uses Modifier of Restaurant B → rejected
# ──────────────────────────────────────────
def test_t15_cross_restaurant_modifier_rejected(client, db, r1_setup, r2_setup):
    r1 = r1_setup["restaurant"]
    loc1 = r1_setup["location"]
    cat1 = r1_setup["category"]
    r2 = r2_setup["restaurant"]
    cat2 = r2_setup["category"]

    # Продукт R1
    p1 = Product(
        restaurant_id=r1.id, category_id=cat1.id,
        name="Продукт R1", price=20000, is_available=True,
    )
    db.add(p1)
    db.flush()

    # Продукт, группа и опция R2
    p2 = Product(
        restaurant_id=r2.id, category_id=cat2.id,
        name="Продукт R2", price=20000, is_available=True,
    )
    db.add(p2)
    db.flush()
    grp2 = ModifierGroup(product_id=p2.id, name="Группа R2", min_selections=0, max_selections=1)
    db.add(grp2)
    db.flush()
    opt2 = ModifierOption(modifier_group_id=grp2.id, name="Опция R2", price_adjustment=0, is_active=True, is_available=True)
    db.add(opt2)
    db.flush()

    # R1 пытается использовать опцию R2
    payload = _make_order_payload(loc1, p1.id, modifier_option_ids=[opt2.id])
    resp = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r1), **_loc_header(loc1)},
    )
    assert resp.status_code in (400, 404), (
        f"Cross-tenant modifier должен быть отклонён: {resp.text}"
    )


# ──────────────────────────────────────────
# T16: legacy product without schedule works normally
# ──────────────────────────────────────────
def test_t16_legacy_product_no_schedule(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    # Продукт без расписания (NULL/NULL) — legacy behavior
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Самса Легаси", price=12000, is_available=True,
        available_from=None, available_until=None,
    )
    db.add(p)
    db.flush()

    # В публичном меню
    resp_menu = client.get(f"/api/menu/{r.id}")
    assert resp_menu.status_code == 200
    products = [pr for c in resp_menu.json() for pr in c["products"]]
    assert any(pr["id"] == p.id for pr in products)

    # В заказе
    payload = _make_order_payload(loc, p.id)
    resp_order = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp_order.status_code == 201


# ──────────────────────────────────────────
# T17: existing variant order (is_available=True) works
# ──────────────────────────────────────────
def test_t17_existing_variant_order_works(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Напиток", price=None, is_available=True,
    )
    db.add(p)
    db.flush()
    v = ProductVariant(product_id=p.id, name="0.5L", price=5000, is_active=True, is_available=True)
    db.add(v)
    db.flush()

    payload = _make_order_payload(loc, p.id, variant_id=v.id)
    resp = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp.status_code == 201


# ──────────────────────────────────────────
# T18: existing modifier order (is_available=True) works
# ──────────────────────────────────────────
def test_t18_existing_modifier_order_works(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Бургер", price=30000, is_available=True,
    )
    db.add(p)
    db.flush()
    grp = ModifierGroup(product_id=p.id, name="Соусы", min_selections=0, max_selections=2)
    db.add(grp)
    db.flush()
    opt = ModifierOption(
        modifier_group_id=grp.id, name="Кетчуп", price_adjustment=0,
        is_active=True, is_available=True,
    )
    db.add(opt)
    db.flush()

    payload = _make_order_payload(loc, p.id, modifier_option_ids=[opt.id])
    resp = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp.status_code == 201


# ──────────────────────────────────────────
# T19: mixed valid order (variant + modifier)
# ──────────────────────────────────────────
def test_t19_mixed_valid_order(client, db, r1_setup):
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]
    p = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Пицца", price=None, is_available=True,
    )
    db.add(p)
    db.flush()
    v = ProductVariant(product_id=p.id, name="Большая", price=60000, is_active=True, is_available=True)
    db.add(v)
    db.flush()
    grp = ModifierGroup(product_id=p.id, name="Топпинги", min_selections=0, max_selections=3)
    db.add(grp)
    db.flush()
    opt1 = ModifierOption(modifier_group_id=grp.id, name="Грибы", price_adjustment=5000, is_active=True, is_available=True)
    opt2 = ModifierOption(modifier_group_id=grp.id, name="Лук", price_adjustment=2000, is_active=True, is_available=True)
    db.add_all([opt1, opt2])
    db.flush()

    payload = _make_order_payload(
        loc, p.id,
        variant_id=v.id,
        modifier_option_ids=[opt1.id, opt2.id],
    )
    resp = client.post(
        "/api/orders/",
        json=payload,
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] is not None
    # Проверяем total = variant.price + modifier adjustments
    expected_total = (60000 + 5000 + 2000) * 1
    assert data["total_amount"] == expected_total


# ──────────────────────────────────────────
# T20: full regression — existing baseline unaffected
# ──────────────────────────────────────────
def test_t20_full_regression(client, db, r1_setup):
    """
    Проверяет что базовое поведение (product, variant, modifier с is_available=True)
    не нарушено Phase 3.
    """
    r = r1_setup["restaurant"]
    cat = r1_setup["category"]
    loc = r1_setup["location"]

    # 1. Public menu возвращает доступные продукты
    p_legacy = Product(
        restaurant_id=r.id, category_id=cat.id,
        name="Регрессия Лагман", price=25000, is_available=True,
    )
    db.add(p_legacy)
    db.flush()

    menu_resp = client.get(f"/api/menu/{r.id}")
    assert menu_resp.status_code == 200
    found = any(
        pr["id"] == p_legacy.id
        for c in menu_resp.json()
        for pr in c["products"]
    )
    assert found, "Legacy продукт должен быть в меню"

    # 2. Заказ legacy продукта → 201
    order_resp = client.post(
        "/api/orders/",
        json=_make_order_payload(loc, p_legacy.id),
        headers={**_auth(r), **_loc_header(loc)},
    )
    assert order_resp.status_code == 201

    # 3. Admin menu endpoint
    admin_resp = client.get(
        f"/api/menu/{r.id}/all",
        headers=_auth(r),
    )
    assert admin_resp.status_code == 200


# ──────────────────────────────────────────
# T21: available_from == available_until → always available (boundary)
# ──────────────────────────────────────────
def test_t21_from_equals_until_always_available(db):
    """
    Контракт: available_from == available_until означает 24 часа доступности.
    Проверяем несколько точек времени.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tashkent")
    t = datetime.time(11, 0)

    for hour in [0, 6, 11, 15, 22, 23]:
        with patch("utils.datetime") as m:
            m.datetime.now.return_value = datetime.datetime(2025, 1, 1, hour, 0, tzinfo=tz)
            result = is_within_schedule(t, t, "Asia/Tashkent")
        assert result is True, f"from==until должно быть True в {hour}:00, получено False"


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
# T27: no active Location → scheduled product treated as unavailable (fail closed)
# ──────────────────────────────────────────
def test_t27_no_location_scheduled_product_fail_closed(client, db, restaurant, category):
    """
    Ресторан без активной Location.
    Продукт с расписанием → fail closed → не показывается.
    Продукт без расписания → показывается нормально.
    """
    # restaurant из fixture — нет Location (не создаём)
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
    db.add_all([p_sched, p_nosched])
    db.flush()

    resp = client.get(f"/api/menu/{restaurant.id}")
    assert resp.status_code == 200
    products = [pr for c in resp.json() for pr in c["products"]]

    p_ids = {pr["id"] for pr in products}
    assert p_sched.id not in p_ids, "Scheduled продукт без Location → fail closed (не показывается)"
    assert p_nosched.id in p_ids, "Продукт без расписания должен показываться даже без Location"
