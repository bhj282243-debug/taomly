"""
tests/test_s4_1_menu_localization.py — Phase 4: Menu Localization

Тест-матрица по контракту v1.1:
  U  — Unit / fallback logic
  L  — Localization (оба публичных endpoint: [M]=menu, [R]=restaurants)
  F  — Fallback scenarios
  C  — CRUD / upsert
  T  — Tenant isolation
  D  — Data integrity / cascade
  V  — Invalid language
  A  — Admin response (/all)
  R  — Regression Phase 2-3

Обозначения endpoint:
  [M] = GET /api/menu/{restaurant_id}?lang=...
  [R] = GET /api/restaurants/{slug}?lang=...
"""

import pytest
from fastapi.testclient import TestClient

from models import (
    Category, CategoryTranslation,
    ModifierGroup, ModifierGroupTranslation,
    ModifierOption, ModifierOptionTranslation,
    Product, ProductTranslation,
    ProductVariant, VariantTranslation,
)


# ──────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────

@pytest.fixture
def cat(db, restaurant):
    """Категория для тестов локализации."""
    c = Category(
        restaurant_id=restaurant.id,
        name="Base Category",
        sort_order=1,
    )
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def prod(db, restaurant, cat):
    """Продукт для тестов локализации."""
    p = Product(
        restaurant_id=restaurant.id,
        category_id=cat.id,
        name="Base Product",
        description="Base description",
        price=10000,
        is_available=True,
        sort_order=1,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def prod_no_desc(db, restaurant, cat):
    """Продукт без description для fallback тестов."""
    p = Product(
        restaurant_id=restaurant.id,
        category_id=cat.id,
        name="No Desc Product",
        description=None,
        price=5000,
        is_available=True,
        sort_order=2,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def variant(db, prod):
    v = ProductVariant(
        product_id=prod.id,
        name="Base Variant",
        price=12000,
        sort_order=1,
        is_active=True,
        is_available=True,
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def mod_group(db, prod):
    g = ModifierGroup(
        product_id=prod.id,
        name="Base Group",
        min_selections=0,
        max_selections=2,
        sort_order=1,
        is_active=True,
    )
    db.add(g)
    db.flush()
    return g


@pytest.fixture
def mod_option(db, mod_group):
    o = ModifierOption(
        modifier_group_id=mod_group.id,
        name="Base Option",
        price_adjustment=0,
        sort_order=1,
        is_active=True,
        is_available=True,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def cat2(db, restaurant2):
    """Категория второго ресторана для tenant isolation."""
    c = Category(
        restaurant_id=restaurant2.id,
        name="Restaurant2 Category",
        sort_order=1,
    )
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def prod2(db, restaurant2, cat2):
    """Продукт второго ресторана для tenant isolation."""
    p = Product(
        restaurant_id=restaurant2.id,
        category_id=cat2.id,
        name="Restaurant2 Product",
        price=10000,
        is_available=True,
        sort_order=1,
    )
    db.add(p)
    db.flush()
    return p


def _add_cat_translation(db, category_id, language, name):
    t = CategoryTranslation(category_id=category_id, language=language, name=name)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _add_prod_translation(db, product_id, language, name, description=None):
    t = ProductTranslation(product_id=product_id, language=language, name=name, description=description)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _add_variant_translation(db, variant_id, language, name):
    t = VariantTranslation(variant_id=variant_id, language=language, name=name)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _add_group_translation(db, group_id, language, name):
    t = ModifierGroupTranslation(modifier_group_id=group_id, language=language, name=name)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _add_option_translation(db, option_id, language, name):
    t = ModifierOptionTranslation(modifier_option_id=option_id, language=language, name=name)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ──────────────────────────────────────────
# U — UNIT / FALLBACK LOGIC
# ──────────────────────────────────────────

class TestUnitFallback:
    """U1-U5: Fallback logic через публичное меню без переводов."""

    def test_u1_no_translation_returns_base_name(self, client, restaurant, cat, prod, db):
        """U1: Нет перевода → entity.name как fallback."""
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        cats = r.json()
        assert any(c["name"] == "Base Category" for c in cats)

    def test_u2_no_translation_description_null_product(self, client, restaurant, prod_no_desc, db):
        """U2: Нет перевода, description=None → description=null."""
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        cats = r.json()
        products = [p for c in cats for p in c["products"]]
        target = next((p for p in products if p["name"] == "No Desc Product"), None)
        if target:
            assert target["description"] is None

    def test_u3_no_translation_description_base(self, client, restaurant, prod, db):
        """U3: Нет перевода, description="Base description" → description возвращается."""
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        cats = r.json()
        products = [p for c in cats for p in c["products"]]
        target = next((p for p in products if p["name"] == "Base Product"), None)
        if target:
            assert target["description"] == "Base description"

    def test_u4_translation_overrides_base_name(self, client, restaurant, cat, prod, db):
        """U4: Перевод найден → translation.name используется, entity.name игнорируется."""
        _add_cat_translation(db, cat.id, "uz", "Asosiy toifa")
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        cats = r.json()
        assert any(c["name"] == "Asosiy toifa" for c in cats)
        assert not any(c["name"] == "Base Category" for c in cats)

    def test_u5_translation_description_overrides_base(self, client, restaurant, cat, prod, db):
        """U5: Translation с description → description из перевода."""
        _add_cat_translation(db, cat.id, "ru", "Основная категория")
        _add_prod_translation(db, prod.id, "ru", "Базовый продукт", "Описание на русском")
        r = client.get(f"/api/menu/{restaurant.id}?lang=ru")
        assert r.status_code == 200
        cats = r.json()
        products = [p for c in cats for p in c["products"]]
        target = next((p for p in products if "Базовый" in p["name"]), None)
        assert target is not None
        assert target["description"] == "Описание на русском"


# ──────────────────────────────────────────
# L — LOCALIZATION (оба публичных endpoint)
# ──────────────────────────────────────────

class TestLocalizationMenuEndpoint:
    """L1a-L8a: GET /api/menu/{restaurant_id}?lang=..."""

    def test_l1a_category_name_uz(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "uz", "Toifa UZ")
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        assert any(c["name"] == "Toifa UZ" for c in r.json())

    def test_l2a_category_name_ru(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "ru", "Категория RU")
        r = client.get(f"/api/menu/{restaurant.id}?lang=ru")
        assert r.status_code == 200
        assert any(c["name"] == "Категория RU" for c in r.json())

    def test_l3a_category_name_en(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "en", "Category EN")
        r = client.get(f"/api/menu/{restaurant.id}?lang=en")
        assert r.status_code == 200
        assert any(c["name"] == "Category EN" for c in r.json())

    def test_l4a_product_name_uz(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "uz", "Toifa")
        _add_prod_translation(db, prod.id, "uz", "Mahsulot UZ")
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        products = [p for c in r.json() for p in c["products"]]
        assert any(p["name"] == "Mahsulot UZ" for p in products)

    def test_l5a_product_description_ru(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "ru", "Кат")
        _add_prod_translation(db, prod.id, "ru", "Продукт RU", "Описание RU")
        r = client.get(f"/api/menu/{restaurant.id}?lang=ru")
        assert r.status_code == 200
        products = [p for c in r.json() for p in c["products"]]
        target = next((p for p in products if p["name"] == "Продукт RU"), None)
        assert target and target["description"] == "Описание RU"

    def test_l6a_variant_name_en(self, client, restaurant, cat, prod, variant, db):
        _add_cat_translation(db, cat.id, "en", "Cat EN")
        _add_prod_translation(db, prod.id, "en", "Product EN")
        _add_variant_translation(db, variant.id, "en", "Variant EN")
        r = client.get(f"/api/menu/{restaurant.id}?lang=en")
        assert r.status_code == 200
        variants = [v for c in r.json() for p in c["products"] for v in p.get("variants", [])]
        assert any(v["name"] == "Variant EN" for v in variants)

    def test_l7a_modifier_group_name_uz(self, client, restaurant, cat, prod, mod_group, mod_option, db):
        _add_cat_translation(db, cat.id, "uz", "Toifa")
        _add_prod_translation(db, prod.id, "uz", "Mahsulot")
        _add_group_translation(db, mod_group.id, "uz", "Guruh UZ")
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        groups = [g for c in r.json() for p in c["products"] for g in p.get("modifier_groups", [])]
        assert any(g["name"] == "Guruh UZ" for g in groups)

    def test_l8a_modifier_option_name_ru(self, client, restaurant, cat, prod, mod_group, mod_option, db):
        _add_cat_translation(db, cat.id, "ru", "Кат")
        _add_prod_translation(db, prod.id, "ru", "Прод")
        _add_group_translation(db, mod_group.id, "ru", "Группа")
        _add_option_translation(db, mod_option.id, "ru", "Опция RU")
        r = client.get(f"/api/menu/{restaurant.id}?lang=ru")
        assert r.status_code == 200
        options = [
            o for c in r.json()
            for p in c["products"]
            for g in p.get("modifier_groups", [])
            for o in g.get("options", [])
        ]
        assert any(o["name"] == "Опция RU" for o in options)


class TestLocalizationRestaurantsEndpoint:
    """L1b-L8b: GET /api/restaurants/{slug}?lang=..."""

    def test_l1b_category_name_uz(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "uz", "Toifa UZ slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=uz")
        assert r.status_code == 200
        cats = r.json().get("categories", [])
        assert any(c["name"] == "Toifa UZ slug" for c in cats)

    def test_l2b_category_name_ru(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "ru", "Категория RU slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=ru")
        assert r.status_code == 200
        cats = r.json().get("categories", [])
        assert any(c["name"] == "Категория RU slug" for c in cats)

    def test_l3b_category_name_en(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "en", "Category EN slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=en")
        assert r.status_code == 200
        cats = r.json().get("categories", [])
        assert any(c["name"] == "Category EN slug" for c in cats)

    def test_l4b_product_name_uz(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "uz", "Toifa")
        _add_prod_translation(db, prod.id, "uz", "Mahsulot UZ slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=uz")
        assert r.status_code == 200
        products = [p for c in r.json().get("categories", []) for p in c.get("products", [])]
        assert any(p["name"] == "Mahsulot UZ slug" for p in products)

    def test_l5b_product_description_ru(self, client, restaurant, cat, prod, db):
        _add_cat_translation(db, cat.id, "ru", "Кат")
        _add_prod_translation(db, prod.id, "ru", "Продукт RU slug", "Описание RU slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=ru")
        assert r.status_code == 200
        products = [p for c in r.json().get("categories", []) for p in c.get("products", [])]
        target = next((p for p in products if "RU slug" in p["name"]), None)
        assert target and target["description"] == "Описание RU slug"

    def test_l6b_variant_name_en(self, client, restaurant, cat, prod, variant, db):
        _add_cat_translation(db, cat.id, "en", "Cat")
        _add_prod_translation(db, prod.id, "en", "Prod")
        _add_variant_translation(db, variant.id, "en", "Variant EN slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=en")
        assert r.status_code == 200
        variants = [v for c in r.json().get("categories",[]) for p in c["products"] for v in p.get("variants",[])]
        assert any(v["name"] == "Variant EN slug" for v in variants)

    def test_l7b_modifier_group_name_uz(self, client, restaurant, cat, prod, mod_group, mod_option, db):
        _add_cat_translation(db, cat.id, "uz", "Toifa")
        _add_prod_translation(db, prod.id, "uz", "Mahsulot")
        _add_group_translation(db, mod_group.id, "uz", "Guruh UZ slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=uz")
        assert r.status_code == 200
        groups = [g for c in r.json().get("categories",[]) for p in c["products"] for g in p.get("modifier_groups",[])]
        assert any(g["name"] == "Guruh UZ slug" for g in groups)

    def test_l8b_modifier_option_name_ru(self, client, restaurant, cat, prod, mod_group, mod_option, db):
        _add_cat_translation(db, cat.id, "ru", "Кат")
        _add_prod_translation(db, prod.id, "ru", "Прод")
        _add_group_translation(db, mod_group.id, "ru", "Группа")
        _add_option_translation(db, mod_option.id, "ru", "Опция RU slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=ru")
        assert r.status_code == 200
        options = [o for c in r.json().get("categories",[]) for p in c["products"] for g in p.get("modifier_groups",[]) for o in g.get("options",[])]
        assert any(o["name"] == "Опция RU slug" for o in options)


# ──────────────────────────────────────────
# F — FALLBACK SCENARIOS
# ──────────────────────────────────────────

class TestFallback:
    def test_f1a_no_uz_translation_fallback_menu(self, client, restaurant, cat, prod, db):
        """F1a [M]: uz запрошен, перевода нет → entity.name."""
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        assert any(c["name"] == "Base Category" for c in r.json())

    def test_f1b_no_uz_translation_fallback_restaurants(self, client, restaurant, cat, prod, db):
        """F1b [R]: uz запрошен, перевода нет → entity.name."""
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=uz")
        assert r.status_code == 200
        cats = r.json().get("categories", [])
        assert any(c["name"] == "Base Category" for c in cats)

    def test_f2a_ru_missing_uz_present_returns_base_menu(self, client, restaurant, cat, prod, db):
        """F2a [M]: ru запрошен, ru нет, uz есть → entity.name (НЕ uz_translation)."""
        _add_cat_translation(db, cat.id, "uz", "Toifa UZ")
        r = client.get(f"/api/menu/{restaurant.id}?lang=ru")
        assert r.status_code == 200
        # должна вернуться base (не uz перевод)
        assert any(c["name"] == "Base Category" for c in r.json())

    def test_f2b_ru_missing_uz_present_returns_base_restaurants(self, client, restaurant, cat, prod, db):
        """F2b [R]: ru запрошен, ru нет, uz есть → entity.name."""
        _add_cat_translation(db, cat.id, "uz", "Toifa UZ slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=ru")
        assert r.status_code == 200
        cats = r.json().get("categories", [])
        assert any(c["name"] == "Base Category" for c in cats)

    def test_f4a_no_translation_null_description_menu(self, client, restaurant, prod_no_desc, db):
        """F4a [M]: description=NULL, нет перевода → null."""
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        products = [p for c in r.json() for p in c["products"]]
        target = next((p for p in products if p["name"] == "No Desc Product"), None)
        if target:
            assert target["description"] is None

    def test_f4b_no_translation_null_description_restaurants(self, client, restaurant, prod_no_desc, db):
        """F4b [R]: description=NULL, нет перевода → null."""
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=uz")
        assert r.status_code == 200
        products = [p for c in r.json().get("categories", []) for p in c.get("products", [])]
        target = next((p for p in products if p["name"] == "No Desc Product"), None)
        if target:
            assert target["description"] is None

    def test_f5a_location_language_ru_used_as_default_menu(self, client, db, restaurant, location, cat, prod):
        """F5a [M]: Location.language="ru", ru перевод есть, без ?lang= → ru translation."""
        location.language = "ru"
        db.commit()
        _add_cat_translation(db, cat.id, "ru", "Категория по Location")
        _add_prod_translation(db, prod.id, "ru", "Продукт по Location")
        r = client.get(f"/api/menu/{restaurant.id}")
        assert r.status_code == 200
        cats = r.json()
        assert any(c["name"] == "Категория по Location" for c in cats)

    def test_f5b_location_language_ru_used_as_default_restaurants(self, client, db, restaurant, location, cat, prod):
        """F5b [R]: Location.language="ru", ru перевод есть, без ?lang= → ru translation."""
        location.language = "ru"
        db.commit()
        _add_cat_translation(db, cat.id, "ru", "Категория по Location slug")
        _add_prod_translation(db, prod.id, "ru", "Продукт по Location slug")
        r = client.get(f"/api/restaurants/{restaurant.slug}")
        assert r.status_code == 200
        cats = r.json().get("categories", [])
        assert any(c["name"] == "Категория по Location slug" for c in cats)


# ──────────────────────────────────────────
# V — INVALID LANGUAGE
# ──────────────────────────────────────────

class TestInvalidLanguage:
    def test_v1_invalid_lang_menu(self, client, restaurant):
        """V1: GET /api/menu/{id}?lang=fr → 422."""
        r = client.get(f"/api/menu/{restaurant.id}?lang=fr")
        assert r.status_code == 422

    def test_v2_invalid_lang_restaurants(self, client, restaurant):
        """V2: GET /api/restaurants/{slug}?lang=xx → 422."""
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=xx")
        assert r.status_code == 422

    def test_v3_invalid_lang_put_translation(self, client, restaurant, cat):
        """V3: PUT category/{id}/translations/fr → 422."""
        r = client.put(
            f"/api/menu/category/{cat.id}/translations/fr",
            json={"name": "Test"},
        )
        assert r.status_code == 422

    def test_v4_no_lang_menu_returns_200(self, client, restaurant, cat, prod):
        """V4: GET /api/menu/{id} без ?lang= → 200 (не 422)."""
        r = client.get(f"/api/menu/{restaurant.id}")
        assert r.status_code == 200

    def test_v5_no_lang_restaurants_returns_200(self, client, restaurant, cat, prod):
        """V5: GET /api/restaurants/{slug} без ?lang= → 200."""
        r = client.get(f"/api/restaurants/{restaurant.slug}")
        assert r.status_code == 200


# ──────────────────────────────────────────
# C — CRUD / UPSERT
# ──────────────────────────────────────────

class TestCRUD:
    def test_c1_create_category_translation(self, client, cat):
        """C1: PUT создаёт перевод → 200."""
        r = client.put(
            f"/api/menu/category/{cat.id}/translations/uz",
            json={"name": "Yangi toifa"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Yangi toifa"
        assert r.json()["language"] == "uz"

    def test_c2_update_category_translation_idempotent(self, client, cat):
        """C2: PUT повторно обновляет → 200, без дубликата."""
        client.put(f"/api/menu/category/{cat.id}/translations/uz", json={"name": "First"})
        r = client.put(f"/api/menu/category/{cat.id}/translations/uz", json={"name": "Second"})
        assert r.status_code == 200
        assert r.json()["name"] == "Second"
        # GET должен вернуть ровно 1 запись для uz
        g = client.get(f"/api/menu/category/{cat.id}/translations")
        uz_records = [t for t in g.json() if t["language"] == "uz"]
        assert len(uz_records) == 1
        assert uz_records[0]["name"] == "Second"

    def test_c3_empty_name_rejected(self, client, cat):
        """C4: PUT name="" → 422."""
        r = client.put(
            f"/api/menu/category/{cat.id}/translations/uz",
            json={"name": ""},
        )
        assert r.status_code == 422

    def test_c4_name_too_long_rejected(self, client, cat):
        """C5: PUT name > 255 → 422."""
        r = client.put(
            f"/api/menu/category/{cat.id}/translations/uz",
            json={"name": "A" * 256},
        )
        assert r.status_code == 422

    def test_c5_get_translations_returns_existing(self, client, cat):
        """C6: GET translations возвращает существующие записи."""
        client.put(f"/api/menu/category/{cat.id}/translations/uz", json={"name": "UZ"})
        client.put(f"/api/menu/category/{cat.id}/translations/ru", json={"name": "RU"})
        r = client.get(f"/api/menu/category/{cat.id}/translations")
        assert r.status_code == 200
        langs = {t["language"] for t in r.json()}
        assert "uz" in langs
        assert "ru" in langs

    def test_c6_product_translation_with_description(self, client, prod):
        """C7: PUT product translation с description."""
        r = client.put(
            f"/api/menu/product/{prod.id}/translations/uz",
            json={"name": "Mahsulot", "description": "Tavsif"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Mahsulot"
        assert r.json()["description"] == "Tavsif"

    def test_c7_product_translation_null_description(self, client, prod):
        """C7b: PUT product translation без description → description=null."""
        r = client.put(
            f"/api/menu/product/{prod.id}/translations/ru",
            json={"name": "Продукт"},
        )
        assert r.status_code == 200
        assert r.json()["description"] is None

    def test_c8_variant_translation(self, client, variant):
        """C8: PUT variant translation."""
        r = client.put(
            f"/api/menu/variant/{variant.id}/translations/en",
            json={"name": "Full Portion"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Full Portion"

    def test_c9_modifier_group_translation(self, client, mod_group):
        """C9: PUT modifier-group translation."""
        r = client.put(
            f"/api/menu/modifier-group/{mod_group.id}/translations/uz",
            json={"name": "Qo'shimchalar"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Qo'shimchalar"

    def test_c10_modifier_option_translation(self, client, mod_option):
        """C10: PUT modifier-option translation."""
        r = client.put(
            f"/api/menu/modifier-option/{mod_option.id}/translations/ru",
            json={"name": "Острое"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Острое"

    def test_c11_all_three_languages(self, client, cat):
        """All 3 languages can coexist for one entity."""
        client.put(f"/api/menu/category/{cat.id}/translations/uz", json={"name": "UZ"})
        client.put(f"/api/menu/category/{cat.id}/translations/ru", json={"name": "RU"})
        client.put(f"/api/menu/category/{cat.id}/translations/en", json={"name": "EN"})
        r = client.get(f"/api/menu/category/{cat.id}/translations")
        assert r.status_code == 200
        assert len(r.json()) == 3


# ──────────────────────────────────────────
# T — TENANT ISOLATION
# ──────────────────────────────────────────

class TestTenantIsolation:
    def test_t1_cannot_write_other_restaurant_category(self, client, cat2):
        """T1: PUT translation для категории чужого ресторана → 404."""
        r = client.put(
            f"/api/menu/category/{cat2.id}/translations/uz",
            json={"name": "Hack"},
        )
        assert r.status_code == 404

    def test_t2_cannot_write_other_restaurant_product(self, client, prod2):
        """T2: PUT translation для продукта чужого ресторана → 404."""
        r = client.put(
            f"/api/menu/product/{prod2.id}/translations/uz",
            json={"name": "Hack"},
        )
        assert r.status_code == 404

    def test_t3_cannot_read_other_restaurant_category_translations(self, client, cat2):
        """T3: GET translations для категории чужого ресторана → 404."""
        r = client.get(f"/api/menu/category/{cat2.id}/translations")
        assert r.status_code == 404

    def test_t4_public_menu_does_not_leak_other_restaurant(self, client, restaurant, restaurant2, cat, prod, cat2, prod2, db):
        """T4 [M]: Публичное меню не содержит данные другого ресторана."""
        r = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert r.status_code == 200
        product_ids = {p["id"] for c in r.json() for p in c["products"]}
        assert prod.id in product_ids
        assert prod2.id not in product_ids

    def test_t5_public_restaurants_does_not_leak_other_restaurant(self, client, restaurant, restaurant2, cat, prod, cat2, prod2, db):
        """T5 [R]: /api/restaurants/{slug} не содержит данные другого ресторана."""
        r = client.get(f"/api/restaurants/{restaurant.slug}?lang=uz")
        assert r.status_code == 200
        product_ids = {p["id"] for c in r.json().get("categories", []) for p in c.get("products", [])}
        assert prod.id in product_ids
        assert prod2.id not in product_ids


# ──────────────────────────────────────────
# D — DATA INTEGRITY / CASCADE
# ──────────────────────────────────────────

class TestDataIntegrity:
    def test_d1_cascade_delete_category(self, client, db, restaurant, cat, prod):
        """D1: Удаление Category → cascade удаляет category_translations."""
        _add_cat_translation(db, cat.id, "uz", "To delete")
        count_before = db.query(CategoryTranslation).filter(
            CategoryTranslation.category_id == cat.id
        ).count()
        assert count_before == 1

        r = client.delete(f"/api/menu/category/{cat.id}")
        assert r.status_code == 204

        count_after = db.query(CategoryTranslation).filter(
            CategoryTranslation.category_id == cat.id
        ).count()
        assert count_after == 0

    def test_d2_cascade_delete_product(self, client, db, restaurant, cat, prod):
        """D2: Удаление Product → cascade удаляет product_translations."""
        _add_prod_translation(db, prod.id, "ru", "To delete")
        count_before = db.query(ProductTranslation).filter(
            ProductTranslation.product_id == prod.id
        ).count()
        assert count_before == 1

        r = client.delete(f"/api/menu/product/{prod.id}")
        assert r.status_code == 204

        count_after = db.query(ProductTranslation).filter(
            ProductTranslation.product_id == prod.id
        ).count()
        assert count_after == 0

    def test_d3_upsert_no_duplicate(self, client, db, cat):
        """D3: PUT дважды → одна запись в БД, не дубликат."""
        client.put(f"/api/menu/category/{cat.id}/translations/uz", json={"name": "First"})
        client.put(f"/api/menu/category/{cat.id}/translations/uz", json={"name": "Second"})
        count = db.query(CategoryTranslation).filter(
            CategoryTranslation.category_id == cat.id,
            CategoryTranslation.language == "uz",
        ).count()
        assert count == 1


# ──────────────────────────────────────────
# A — ADMIN RESPONSE (/all endpoint)
# ──────────────────────────────────────────

class TestAdminResponse:
    def test_a1_all_includes_translations_field(self, client, restaurant, cat, prod, db):
        """A1: GET /all включает translations в category."""
        _add_cat_translation(db, cat.id, "uz", "Admin UZ")
        r = client.get(f"/api/menu/{restaurant.id}/all")
        assert r.status_code == 200
        cats = r.json()
        assert len(cats) > 0
        assert "translations" in cats[0]

    def test_a2_translations_empty_when_none(self, client, restaurant, cat, prod):
        """A2: translations=[] если нет записей."""
        r = client.get(f"/api/menu/{restaurant.id}/all")
        assert r.status_code == 200
        cats = r.json()
        assert cats[0]["translations"] == []

    def test_a3_translations_contains_only_existing(self, client, restaurant, cat, prod, db):
        """A3: translations содержит только существующие языки."""
        _add_cat_translation(db, cat.id, "uz", "UZ")
        _add_cat_translation(db, cat.id, "ru", "RU")
        r = client.get(f"/api/menu/{restaurant.id}/all")
        assert r.status_code == 200
        cats = r.json()
        langs = {t["language"] for t in cats[0]["translations"]}
        assert langs == {"uz", "ru"}
        assert "en" not in langs


# ──────────────────────────────────────────
# R — REGRESSION Phase 2 & 3
# ──────────────────────────────────────────

class TestRegression:
    def test_r1_menu_without_lang_returns_200(self, client, restaurant, cat, prod):
        """R1: GET /api/menu/{id} без ?lang= → 200, структура List[CategoryPublicResponse]."""
        r = client.get(f"/api/menu/{restaurant.id}")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        if data:
            assert "name" in data[0]
            assert "products" in data[0]

    def test_r2_restaurants_without_lang_returns_200(self, client, restaurant, cat, prod):
        """R2: GET /api/restaurants/{slug} без ?lang= → 200."""
        r = client.get(f"/api/restaurants/{restaurant.slug}")
        assert r.status_code == 200
        assert "categories" in r.json()

    def test_r5_create_product_without_translations_works(self, client, restaurant, cat):
        """R5: Создание продукта без переводов → меню работает."""
        r = client.post("/api/menu/product/", json={
            "category_id": cat.id,
            "name": "New Product Without Translation",
            "price": 15000,
        })
        assert r.status_code == 201
        product_id = r.json()["id"]

        menu = client.get(f"/api/menu/{restaurant.id}?lang=uz")
        assert menu.status_code == 200
        products = [p for c in menu.json() for p in c["products"]]
        assert any(p["id"] == product_id for p in products)
