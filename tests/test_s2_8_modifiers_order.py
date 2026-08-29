"""
tests/test_s2_8_modifiers_order.py — S2-8: Modifier Selection in Orders

Покрытие:
  T1:  Нет modifier_option_ids, нет обязательных групп → 201, selected_modifiers=[]
  T2:  Валидные modifier_option_ids → 201, snapshot name+price_adjustment
  T3:  Обязательная группа (min_selections=1), ids не переданы → 400
  T4:  Превышен max_selections → 400
  T5:  modifier_option_id из другого ресторана → 400 (tenant isolation P0)
  T6:  modifier_option_id неактивной опции → 400
  T7:  GET /api/menu/{id} — modifier_groups видны в ответе (активные)
  T8:  GET /api/menu/{id} — неактивные modifier_options скрыты
  T9:  GET /api/restaurants/{slug} — modifier_groups в products
  T10: OrderItem.price не меняется при наличии modifier price_adjustment
  T11: Регрессия: legacy-заказ без modifier_option_ids — 201
  T12: Дубликаты modifier_option_ids нормализуются (уникальные)
  T13: Опция из неактивной группы → 400
  T14: Опция из продукта другого ресторана → 400
  T15: Изменение ModifierOption после заказа не меняет snapshot
  T16: Удаление ModifierOption сохраняет OrderItemModifier (SET NULL)
  T17: Mixed order: legacy + variant + modifiers → 201
  T18: Невалидный modifier во втором item → Order не создаётся частично

CI Gate: все тесты должны PASS без новых регрессий.
"""

import pytest
from fastapi.testclient import TestClient

from models import (
    Category,
    ModifierGroup,
    ModifierOption,
    Order,
    OrderItem,
    OrderItemModifier,
    Product,
    ProductVariant,
)


# ──────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def product_mod(db, restaurant, category) -> Product:
    """Продукт с ценой, к которому будем добавлять модификаторы."""
    p = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Бургер Классический",
        price=35000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def product_legacy(db, restaurant, category) -> Product:
    """Legacy-продукт без вариантов и без модификаторов."""
    p = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Самса",
        price=15000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def product_variant_mod(db, restaurant, category) -> Product:
    """Variant-продукт для mixed order теста."""
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
def variant_for_mod(db, product_variant_mod) -> ProductVariant:
    v = ProductVariant(
        product_id=product_variant_mod.id,
        name="Стандарт",
        price=30000,
        sort_order=1,
        is_active=True,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def group_optional(db, product_mod) -> ModifierGroup:
    """Опциональная группа: min=0, max=3."""
    g = ModifierGroup(
        product_id=product_mod.id,
        name="Соусы",
        min_selections=0,
        max_selections=3,
        sort_order=0,
        is_active=True,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def group_required(db, product_mod) -> ModifierGroup:
    """Обязательная группа: min=1, max=1."""
    g = ModifierGroup(
        product_id=product_mod.id,
        name="Степень прожарки",
        min_selections=1,
        max_selections=1,
        sort_order=1,
        is_active=True,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def group_inactive(db, product_mod) -> ModifierGroup:
    """Неактивная группа."""
    g = ModifierGroup(
        product_id=product_mod.id,
        name="Скрытая группа",
        min_selections=0,
        max_selections=2,
        sort_order=9,
        is_active=False,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def opt_ketchup(db, group_optional) -> ModifierOption:
    """Активная опция Кетчуп, price_adjustment=1000."""
    o = ModifierOption(
        modifier_group_id=group_optional.id,
        name="Кетчуп",
        price_adjustment=1000,
        sort_order=0,
        is_active=True,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def opt_mayo(db, group_optional) -> ModifierOption:
    """Активная опция Майонез, price_adjustment=500."""
    o = ModifierOption(
        modifier_group_id=group_optional.id,
        name="Майонез",
        price_adjustment=500,
        sort_order=1,
        is_active=True,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def opt_inactive(db, group_optional) -> ModifierOption:
    """Неактивная опция."""
    o = ModifierOption(
        modifier_group_id=group_optional.id,
        name="Скрытый соус",
        price_adjustment=0,
        sort_order=99,
        is_active=False,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def opt_rare(db, group_required) -> ModifierOption:
    """Опция для обязательной группы: Rare."""
    o = ModifierOption(
        modifier_group_id=group_required.id,
        name="Rare",
        price_adjustment=0,
        sort_order=0,
        is_active=True,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def opt_inactive_group(db, group_inactive) -> ModifierOption:
    """Активная опция в неактивной группе (T13)."""
    o = ModifierOption(
        modifier_group_id=group_inactive.id,
        name="Опция неактивной группы",
        price_adjustment=0,
        sort_order=0,
        is_active=True,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def product_r2(db, restaurant2, category_r2) -> Product:
    """Продукт другого ресторана."""
    p = Product(
        restaurant_id=restaurant2.id,
        category_id=category_r2.id,
        name="Лагман R2",
        price=25000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def category_r2(db, restaurant2) -> Category:
    c = Category(restaurant_id=restaurant2.id, name="Основное", sort_order=1)
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def group_r2(db, product_r2) -> ModifierGroup:
    g = ModifierGroup(
        product_id=product_r2.id,
        name="Острота",
        min_selections=0,
        max_selections=1,
        sort_order=0,
        is_active=True,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def opt_r2(db, group_r2) -> ModifierOption:
    """Опция из ресторана R2."""
    o = ModifierOption(
        modifier_group_id=group_r2.id,
        name="Острый",
        price_adjustment=0,
        sort_order=0,
        is_active=True,
    )
    db.add(o)
    db.flush()
    return o


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _takeaway(items: list) -> dict:
    return {"order_type": "takeaway", "items": items}


def _item(product_id: int, qty: int = 1, modifier_option_ids: list | None = None, variant_id: int | None = None) -> dict:
    d: dict = {"product_id": product_id, "quantity": qty}
    if modifier_option_ids is not None:
        d["modifier_option_ids"] = modifier_option_ids
    if variant_id is not None:
        d["variant_id"] = variant_id
    return d


# ──────────────────────────────────────────────────────────────────────────────
# T1: Нет modifier_option_ids, нет обязательных групп → 201, selected_modifiers=[]
# ──────────────────────────────────────────────────────────────────────────────

def test_t1_no_modifiers_optional_group_201(
    client, product_mod, group_optional, opt_ketchup
):
    """T1: Продукт с опциональной группой, modifier_option_ids не передан → 201."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id),
    ]))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    item = data["items"][0]
    assert item["selected_modifiers"] == []


# ──────────────────────────────────────────────────────────────────────────────
# T2: Валидные modifier_option_ids → 201, snapshot name+price_adjustment
# ──────────────────────────────────────────────────────────────────────────────

def test_t2_valid_modifiers_snapshot_201(
    client, product_mod, group_optional, opt_ketchup, opt_mayo
):
    """T2: Два валидных модификатора → snapshot из БД, не от клиента."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[opt_ketchup.id, opt_mayo.id]),
    ]))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    item = data["items"][0]
    mods = {m["modifier_option_id"]: m for m in item["selected_modifiers"]}

    assert opt_ketchup.id in mods
    assert mods[opt_ketchup.id]["name"] == "Кетчуп"
    assert mods[opt_ketchup.id]["price_adjustment"] == 1000

    assert opt_mayo.id in mods
    assert mods[opt_mayo.id]["name"] == "Майонез"
    assert mods[opt_mayo.id]["price_adjustment"] == 500


# ──────────────────────────────────────────────────────────────────────────────
# T3: Обязательная группа (min_selections=1), ids не переданы → 400
# ──────────────────────────────────────────────────────────────────────────────

def test_t3_required_group_no_selection_400(
    client, product_mod, group_required, opt_rare
):
    """T3: Обязательная группа без выбора → 400."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[]),
    ]))
    assert resp.status_code == 400, resp.text
    assert "обязательна" in resp.json()["detail"] or "минимум" in resp.json()["detail"].lower()


# ──────────────────────────────────────────────────────────────────────────────
# T4: Превышен max_selections → 400
# ──────────────────────────────────────────────────────────────────────────────

def test_t4_max_selections_exceeded_via_required_group_400(
    client, db, product_mod, group_required, opt_rare
):
    """T4: group_required.max_selections=1, передаём 2 опции → 400."""
    opt2 = ModifierOption(
        modifier_group_id=group_required.id,
        name="Well done",
        price_adjustment=0,
        sort_order=1,
        is_active=True,
    )
    db.add(opt2)
    db.flush()

    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[opt_rare.id, opt2.id]),
    ]))
    assert resp.status_code == 400, resp.text
    assert "максимум" in resp.json()["detail"].lower()


# ──────────────────────────────────────────────────────────────────────────────
# T5: modifier_option_id из другого ресторана → 400 (tenant isolation P0)
# ──────────────────────────────────────────────────────────────────────────────

def test_t5_cross_tenant_modifier_400(
    client, product_mod, group_optional, opt_ketchup, opt_r2
):
    """T5: opt_r2 принадлежит ресторану R2, не R1 → 400."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[opt_r2.id]),
    ]))
    assert resp.status_code == 400, resp.text


# ──────────────────────────────────────────────────────────────────────────────
# T6: modifier_option_id неактивной опции → 400
# ──────────────────────────────────────────────────────────────────────────────

def test_t6_inactive_option_400(
    client, product_mod, group_optional, opt_inactive
):
    """T6: Неактивная опция → 400."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[opt_inactive.id]),
    ]))
    assert resp.status_code == 400, resp.text


# ──────────────────────────────────────────────────────────────────────────────
# T7: GET /api/menu/{id} — modifier_groups видны (активные)
# ──────────────────────────────────────────────────────────────────────────────

def test_t7_public_menu_has_modifier_groups(
    client, restaurant, product_mod, group_optional, opt_ketchup
):
    """T7: Публичный эндпоинт меню отдаёт modifier_groups с активными опциями."""
    resp = client.get(f"/api/v1/menu/{restaurant.id}")
    assert resp.status_code == 200, resp.text
    categories = resp.json()
    # Найдём нужный продукт
    products = [p for cat in categories for p in cat["products"] if p["id"] == product_mod.id]
    assert products, "product_mod должен быть в публичном меню"
    p = products[0]
    assert "modifier_groups" in p
    groups = {g["id"]: g for g in p["modifier_groups"]}
    assert group_optional.id in groups
    opts = {o["id"]: o for o in groups[group_optional.id]["options"]}
    assert opt_ketchup.id in opts
    assert opts[opt_ketchup.id]["name"] == "Кетчуп"
    assert opts[opt_ketchup.id]["price_adjustment"] == 1000


# ──────────────────────────────────────────────────────────────────────────────
# T8: GET /api/menu/{id} — неактивные options скрыты
# ──────────────────────────────────────────────────────────────────────────────

def test_t8_inactive_options_hidden_in_public_menu(
    client, restaurant, product_mod, group_optional, opt_ketchup, opt_inactive
):
    """T8: Неактивная опция не видна в публичном меню."""
    resp = client.get(f"/api/v1/menu/{restaurant.id}")
    assert resp.status_code == 200, resp.text
    categories = resp.json()
    products = [p for cat in categories for p in cat["products"] if p["id"] == product_mod.id]
    assert products
    p = products[0]
    groups = {g["id"]: g for g in p["modifier_groups"]}
    assert group_optional.id in groups
    opt_ids = [o["id"] for o in groups[group_optional.id]["options"]]
    assert opt_inactive.id not in opt_ids, "Неактивная опция не должна быть в публичном меню"


# ──────────────────────────────────────────────────────────────────────────────
# T9: GET /api/restaurants/{slug} — modifier_groups в products
# ──────────────────────────────────────────────────────────────────────────────

def test_t9_restaurant_slug_has_modifier_groups(
    client, restaurant, product_mod, group_optional, opt_ketchup
):
    """T9: GET /api/restaurants/{slug} отдаёт modifier_groups."""
    resp = client.get(f"/api/v1/restaurants/{restaurant.slug}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    products = [
        p
        for cat in data.get("categories", [])
        for p in cat.get("products", [])
        if p["id"] == product_mod.id
    ]
    assert products, "product_mod должен быть в slug-эндпоинте"
    p = products[0]
    assert "modifier_groups" in p
    group_ids = [g["id"] for g in p["modifier_groups"]]
    assert group_optional.id in group_ids


# ──────────────────────────────────────────────────────────────────────────────
# T10: OrderItem.price НЕ меняется от price_adjustment
# ──────────────────────────────────────────────────────────────────────────────

def test_t10_order_item_price_unchanged_with_modifiers(
    client, product_mod, group_optional, opt_ketchup
):
    """T10: price_adjustment не влияет на OrderItem.price (Phase 7)."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[opt_ketchup.id]),
    ]))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    item = data["items"][0]
    # product_mod.price = 35000, opt_ketchup.price_adjustment = 1000
    # OrderItem.price должен оставаться 35000 (не 36000)
    assert item["price"] == 35000, f"price должен быть 35000, получили {item['price']}"


# ──────────────────────────────────────────────────────────────────────────────
# T11: Legacy-заказ без modifier_option_ids — backward compatible
# ──────────────────────────────────────────────────────────────────────────────

def test_t11_legacy_order_no_modifiers_backward_compat(client, product_legacy):
    """T11: Существующий формат без modifier_option_ids работает."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        {"product_id": product_legacy.id, "quantity": 2},
    ]))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    item = data["items"][0]
    assert item["selected_modifiers"] == []
    assert item["price"] == 15000


# ──────────────────────────────────────────────────────────────────────────────
# T12: Дубликаты modifier_option_ids нормализуются
# ──────────────────────────────────────────────────────────────────────────────

def test_t12_duplicate_modifier_ids_normalized(
    client, product_mod, group_optional, opt_ketchup
):
    """T12: [ketchup, ketchup, ketchup] → одна запись в selected_modifiers."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[
            opt_ketchup.id, opt_ketchup.id, opt_ketchup.id
        ]),
    ]))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    item = data["items"][0]
    ketchup_mods = [m for m in item["selected_modifiers"] if m["modifier_option_id"] == opt_ketchup.id]
    assert len(ketchup_mods) == 1, "Дубликаты должны быть нормализованы до одной записи"


# ──────────────────────────────────────────────────────────────────────────────
# T13: Опция из неактивной группы → 400
# ──────────────────────────────────────────────────────────────────────────────

def test_t13_option_from_inactive_group_400(
    client, product_mod, group_inactive, opt_inactive_group
):
    """T13: Группа неактивна, опция активна → 400."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[opt_inactive_group.id]),
    ]))
    assert resp.status_code == 400, resp.text
    assert "неактивной группе" in resp.json()["detail"]


# ──────────────────────────────────────────────────────────────────────────────
# T14: Опция из другого продукта того же ресторана → 400
# ──────────────────────────────────────────────────────────────────────────────

def test_t14_option_from_different_product_same_restaurant_400(
    client, db, restaurant, category, product_mod, group_optional, opt_ketchup
):
    """T14: Опция принадлежит другому продукту (не product_mod) → 400."""
    # Создаём отдельный продукт и его группу
    other_product = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Другое блюдо",
        price=20000,
        is_available=True,
    )
    db.add(other_product)
    db.flush()
    other_group = ModifierGroup(
        product_id=other_product.id,
        name="Специи",
        min_selections=0,
        max_selections=1,
        sort_order=0,
        is_active=True,
    )
    db.add(other_group)
    db.flush()
    other_opt = ModifierOption(
        modifier_group_id=other_group.id,
        name="Перец",
        price_adjustment=0,
        sort_order=0,
        is_active=True,
    )
    db.add(other_opt)
    db.flush()

    # other_opt принадлежит other_product, не product_mod → tenant P0 reject
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[other_opt.id]),
    ]))
    assert resp.status_code == 400, resp.text


# ──────────────────────────────────────────────────────────────────────────────
# T15: Изменение ModifierOption после заказа не меняет snapshot
# ──────────────────────────────────────────────────────────────────────────────

def test_t15_snapshot_immutable_after_option_change(
    client, db, product_mod, group_optional, opt_ketchup
):
    """T15: Snapshot зафиксирован на момент заказа — изменение опции не влияет."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[opt_ketchup.id]),
    ]))
    assert resp.status_code == 201, resp.text
    order_data = resp.json()
    item = order_data["items"][0]
    snap = next(m for m in item["selected_modifiers"] if m["modifier_option_id"] == opt_ketchup.id)
    original_price_adj = snap["price_adjustment"]
    original_name = snap["name"]

    # Меняем ModifierOption в БД
    opt_ketchup.price_adjustment = 9999
    opt_ketchup.name = "Кетчуп Премиум"
    db.flush()

    # Перечитываем OrderItemModifier из БД напрямую
    oim = db.query(OrderItemModifier).filter(
        OrderItemModifier.modifier_option_id == opt_ketchup.id
    ).first()
    assert oim is not None
    assert oim.price_adjustment == original_price_adj, "Snapshot не должен меняться"
    assert oim.name == original_name, "Snapshot имени не должен меняться"


# ──────────────────────────────────────────────────────────────────────────────
# T16: Удаление ModifierOption → OrderItemModifier сохраняется (SET NULL)
# ──────────────────────────────────────────────────────────────────────────────

def test_t16_delete_option_preserves_snapshot(
    client, db, product_mod, group_optional, opt_ketchup
):
    """T16: После удаления ModifierOption запись OrderItemModifier остаётся, modifier_option_id=NULL."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        _item(product_mod.id, modifier_option_ids=[opt_ketchup.id]),
    ]))
    assert resp.status_code == 201, resp.text
    order_data = resp.json()
    item_id = order_data["items"][0]["id"]

    # Запоминаем данные snapshot до удаления
    oim_before = db.query(OrderItemModifier).filter(
        OrderItemModifier.order_item_id == item_id
    ).first()
    assert oim_before is not None
    saved_name = oim_before.name
    saved_adj = oim_before.price_adjustment
    oim_id = oim_before.id

    # Удаляем ModifierOption
    db.delete(opt_ketchup)
    db.flush()

    # OrderItemModifier должен остаться с modifier_option_id=NULL
    db.expire_all()
    oim_after = db.query(OrderItemModifier).filter(OrderItemModifier.id == oim_id).first()
    assert oim_after is not None, "OrderItemModifier должен остаться после удаления опции"
    assert oim_after.modifier_option_id is None, "modifier_option_id должен стать NULL"
    assert oim_after.name == saved_name, "name snapshot должен сохраниться"
    assert oim_after.price_adjustment == saved_adj, "price_adjustment snapshot должен сохраниться"


# ──────────────────────────────────────────────────────────────────────────────
# T17: Mixed order: legacy + variant + modifiers → 201
# ──────────────────────────────────────────────────────────────────────────────

def test_t17_mixed_order_legacy_variant_modifiers_201(
    client,
    product_legacy,
    product_mod,
    product_variant_mod,
    variant_for_mod,
    group_optional,
    opt_ketchup,
):
    """T17: Заказ с тремя items: legacy, variant, product+modifier → 201."""
    resp = client.post("/api/v1/orders/", json=_takeaway([
        # Legacy item без модификаторов
        _item(product_legacy.id, qty=2),
        # Variant item без модификаторов
        _item(product_variant_mod.id, qty=1, variant_id=variant_for_mod.id),
        # Modifier item с кетчупом
        _item(product_mod.id, qty=1, modifier_option_ids=[opt_ketchup.id]),
    ]))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert len(data["items"]) == 3

    by_product = {item["name"]: item for item in data["items"]}
    assert by_product["Самса"]["selected_modifiers"] == []
    assert by_product["Плов"]["selected_modifiers"] == []
    ketchup_mods = by_product["Бургер Классический"]["selected_modifiers"]
    assert len(ketchup_mods) == 1
    assert ketchup_mods[0]["name"] == "Кетчуп"


# ──────────────────────────────────────────────────────────────────────────────
# T18: Невалидный modifier во втором item → ничего не создаётся
# ──────────────────────────────────────────────────────────────────────────────

def test_t18_invalid_modifier_second_item_no_partial_order(
    client, db, product_legacy, product_mod, group_optional, opt_inactive
):
    """T18: Первый item валиден, второй — невалидный модификатор → Order не создаётся частично."""
    orders_before = db.query(Order).count()
    items_before = db.query(OrderItem).count()

    resp = client.post("/api/v1/orders/", json=_takeaway([
        # Первый item — валидный legacy
        _item(product_legacy.id, qty=1),
        # Второй item — невалидный modifier (неактивная опция)
        _item(product_mod.id, modifier_option_ids=[opt_inactive.id]),
    ]))
    assert resp.status_code == 400, resp.text

    # БД должна остаться нетронутой
    db.expire_all()
    assert db.query(Order).count() == orders_before, "Order не должен был создаться"
    assert db.query(OrderItem).count() == items_before, "OrderItem не должен был создаться"
