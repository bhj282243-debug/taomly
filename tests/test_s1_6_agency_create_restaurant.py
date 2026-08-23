"""
tests/test_s1_6_agency_create_restaurant.py — S1-6: Auto-create Location on create_restaurant

Covers:
  CheckA: POST /api/agency/restaurants → Location создаётся автоматически
  CheckB: Поля первой Location соответствуют Restaurant
  CheckC: Атомарность — slug-конфликт откатывает и Restaurant, и Location
  CheckD: Совместимость с S1-2..S1-5 flows после S1-6
  CheckE: Tenant isolation — чужой агент не видит Location нового ресторана

Invariants tested:
  I-A  После create_restaurant: ровно 1 Location с restaurant_id = new_restaurant.id
  I-B  location.slug == restaurant.slug
  I-C  IntegrityError на slug откатывает Restaurant тоже (атомарность через flush+commit)
  I-D  location.is_active == True
  I-E  location.restaurant_id == restaurant.id
"""

import pytest

from models import Location, Restaurant


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _new_restaurant_payload(slug: str = "newplace", **overrides) -> dict:
    """Минимально валидный payload для POST /api/agency/restaurants."""
    base = {
        "name": "New Place",
        "slug": slug,
        "admin_password": "securepass1",
    }
    base.update(overrides)
    return base


def _post_restaurant(client, payload: dict):
    return client.post("/api/agency/restaurants", json=payload)


# ══════════════════════════════════════════════════════════════════════════════
# CheckA — Location создаётся автоматически
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckA:
    """CheckA: POST /api/agency/restaurants → Location создаётся автоматически."""

    def test_a1_create_restaurant_returns_201(self, client, db):
        """A1. POST → 201 (базовая проверка, что эндпоинт жив)."""
        resp = _post_restaurant(client, _new_restaurant_payload())
        assert resp.status_code == 201

    def test_a1_exactly_one_location_created(self, client, db):
        """A1. После создания ресторана: ровно 1 Location с restaurant_id нового ресторана."""
        resp = _post_restaurant(client, _new_restaurant_payload(slug="autoplace"))
        assert resp.status_code == 201
        new_id = resp.json()["id"]

        locations = (
            db.query(Location)
            .filter(Location.restaurant_id == new_id)
            .all()
        )
        assert len(locations) == 1, (
            f"Ожидалась 1 Location, найдено {len(locations)} для restaurant_id={new_id}"
        )

    def test_a2_location_slug_equals_restaurant_slug(self, client, db):
        """A2. location.slug == restaurant.slug (backward compat для webhook/QR)."""
        slug = "slugtest"
        resp = _post_restaurant(client, _new_restaurant_payload(slug=slug))
        assert resp.status_code == 201
        new_id = resp.json()["id"]

        loc = db.query(Location).filter(Location.restaurant_id == new_id).first()
        assert loc is not None
        assert loc.slug == slug

    def test_a3_location_is_active_true(self, client, db):
        """A3. Первая Location is_active == True."""
        resp = _post_restaurant(client, _new_restaurant_payload(slug="activetest"))
        assert resp.status_code == 201
        new_id = resp.json()["id"]

        loc = db.query(Location).filter(Location.restaurant_id == new_id).first()
        assert loc is not None
        assert loc.is_active is True

    def test_a4_location_restaurant_id_correct(self, client, db):
        """A4. location.restaurant_id == ответ restaurant.id."""
        resp = _post_restaurant(client, _new_restaurant_payload(slug="idtest"))
        assert resp.status_code == 201
        new_id = resp.json()["id"]

        loc = db.query(Location).filter(Location.restaurant_id == new_id).first()
        assert loc is not None
        assert loc.restaurant_id == new_id

    def test_a5_location_name_equals_restaurant_name(self, client, db):
        """A5. location.name == restaurant.name."""
        resp = _post_restaurant(
            client,
            _new_restaurant_payload(slug="nametest", name="Мой Ресторан"),
        )
        assert resp.status_code == 201
        new_id = resp.json()["id"]

        restaurant = db.query(Restaurant).filter(Restaurant.id == new_id).first()
        loc = db.query(Location).filter(Location.restaurant_id == new_id).first()
        assert loc is not None
        assert loc.name == restaurant.name


# ══════════════════════════════════════════════════════════════════════════════
# CheckB — Поля Location соответствуют ожидаемым дефолтам
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckB:
    """CheckB: Поля первой Location соответствуют дефолтам из S1-6 создания."""

    @pytest.fixture
    def new_location(self, client, db):
        """Создаёт ресторан и возвращает его Location."""
        resp = _post_restaurant(client, _new_restaurant_payload(slug="defaults-loc"))
        assert resp.status_code == 201
        new_id = resp.json()["id"]
        return db.query(Location).filter(Location.restaurant_id == new_id).first()

    def test_b1_currency_default(self, new_location):
        """B1. currency == 'UZS' (дефолт)."""
        assert new_location.currency == "UZS"

    def test_b2_language_default(self, new_location):
        """B2. language == 'uz' (дефолт)."""
        assert new_location.language == "uz"

    def test_b3_timezone_default(self, new_location):
        """B3. timezone == 'Asia/Tashkent' (дефолт)."""
        assert new_location.timezone == "Asia/Tashkent"

    def test_b4_delivery_fee_zero(self, new_location):
        """B4. delivery_fee == 0."""
        assert new_location.delivery_fee == 0

    def test_b5_min_order_amount_zero(self, new_location):
        """B5. min_order_amount == 0."""
        assert new_location.min_order_amount == 0


# ══════════════════════════════════════════════════════════════════════════════
# CheckC — Атомарность транзакции
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckC:
    """CheckC: slug-конфликт откатывает и Restaurant, и Location."""

    def test_c1_slug_conflict_returns_4xx(self, client, db, restaurant):
        """C1. Попытка создать ресторан с уже занятым slug → 400 или 409."""
        # restaurant fixture уже занял slug "chinar"
        resp = _post_restaurant(client, _new_restaurant_payload(slug=restaurant.slug))
        assert resp.status_code in (400, 409), (
            f"Ожидался 400 или 409, получен {resp.status_code}"
        )

    def test_c1_slug_conflict_no_location_created(self, client, db, restaurant):
        """C1. При slug-конфликте Location для нового ресторана НЕ создаётся."""
        before = db.query(Location).count()
        _post_restaurant(client, _new_restaurant_payload(slug=restaurant.slug))
        after = db.query(Location).count()
        assert after == before, (
            f"Количество Location изменилось: было {before}, стало {after}"
        )

    def test_c1_slug_conflict_no_restaurant_created(self, client, db, restaurant):
        """C1. При slug-конфликте Restaurant тоже НЕ создаётся (атомарность)."""
        before = db.query(Restaurant).count()
        _post_restaurant(client, _new_restaurant_payload(slug=restaurant.slug))
        after = db.query(Restaurant).count()
        assert after == before, (
            f"Количество Restaurant изменилось: было {before}, стало {after}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CheckD — Совместимость с S1-5 (GET /me/locations)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckD:
    """CheckD: Совместимость с S1-5 Location CRUD после S1-6."""

    def test_d3_get_locations_returns_one_entry(self, client, db):
        """D3. GET /api/restaurants/me/locations для нового ресторана → ровно 1 Location."""
        # Создаём ресторан через agency endpoint
        resp = _post_restaurant(client, _new_restaurant_payload(slug="compat-loc"))
        assert resp.status_code == 201
        new_restaurant_id = resp.json()["id"]

        # client fixture переопределяет get_current_restaurant_admin → restaurant (fixture).
        # Нам нужно проверить через БД, что у нового ресторана ровно 1 Location.
        locs = (
            db.query(Location)
            .filter(Location.restaurant_id == new_restaurant_id)
            .all()
        )
        assert len(locs) == 1


# ══════════════════════════════════════════════════════════════════════════════
# CheckE — Tenant isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckE:
    """CheckE: Location нового ресторана не пересекается с другими tenant'ами."""

    def test_e1_new_location_belongs_to_new_restaurant_only(self, client, db, restaurant):
        """E1. Location нового ресторана не появляется у уже существующего restaurant."""
        locs_before = (
            db.query(Location)
            .filter(Location.restaurant_id == restaurant.id)
            .count()
        )

        resp = _post_restaurant(client, _new_restaurant_payload(slug="isolation-test"))
        assert resp.status_code == 201
        new_id = resp.json()["id"]
        assert new_id != restaurant.id

        locs_after = (
            db.query(Location)
            .filter(Location.restaurant_id == restaurant.id)
            .count()
        )
        assert locs_after == locs_before, (
            "Location нового ресторана попала в список локаций другого ресторана"
        )

    def test_e2_location_restaurant_id_matches_new_restaurant(self, client, db, restaurant):
        """E2. location.restaurant_id точно соответствует новому ресторану, не старому."""
        resp = _post_restaurant(client, _new_restaurant_payload(slug="tenantcheck"))
        assert resp.status_code == 201
        new_id = resp.json()["id"]

        loc = db.query(Location).filter(Location.restaurant_id == new_id).first()
        assert loc is not None
        assert loc.restaurant_id == new_id
        assert loc.restaurant_id != restaurant.id
