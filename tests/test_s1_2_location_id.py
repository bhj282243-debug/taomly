"""
tests/test_s1_2_location_id.py — S1-2: restaurant_tables.location_id

Проверки:
  A. location_id IS NULL = 0
  B. Cross-brand consistency: rt.restaurant_id == l.restaurant_id
  C. Нет дублирующих table_number внутри одной Location
  D. Один table_number разрешён в разных Locations (ADR — ключевой сценарий)
  E. location_id заполняется при создании через fixture
  F. location relationship работает
  G. Legacy restaurant_id присутствует (не удалён)
  H. Только uq_table_location_number в модели (uq_table_restaurant_number удалён)
  I. Дубль в одной Location → IntegrityError
  J. Тот же table_number в двух Locations одного Brand → PASS (regression)
"""

import pytest
from sqlalchemy.exc import IntegrityError

from models import Location, Restaurant, RestaurantTable


# ── A. Все строки имеют location_id ───────────────────────────────────────────
class TestCheckA:
    def test_no_null_location_id(self, db, table):
        null_count = (
            db.query(RestaurantTable)
            .filter(RestaurantTable.location_id.is_(None))
            .count()
        )
        assert null_count == 0, f"A FAIL: {null_count} rows have location_id IS NULL"


# ── B. Cross-brand consistency ─────────────────────────────────────────────────
class TestCheckB:
    def test_cross_brand_consistency(self, db, table):
        bad = (
            db.query(RestaurantTable)
            .join(Location, Location.id == RestaurantTable.location_id)
            .filter(RestaurantTable.restaurant_id != Location.restaurant_id)
            .all()
        )
        assert len(bad) == 0, f"B FAIL: {len(bad)} cross-brand rows"


# ── C. Нет дублирующих table_number внутри одной Location ─────────────────────
class TestCheckC:
    def test_no_duplicate_within_location(self, db, table):
        from sqlalchemy import func
        dups = (
            db.query(RestaurantTable.location_id, RestaurantTable.table_number)
            .group_by(RestaurantTable.location_id, RestaurantTable.table_number)
            .having(func.count() > 1)
            .all()
        )
        assert len(dups) == 0, f"C FAIL: duplicates within location: {dups}"


# ── D. Один table_number разрешён в разных Locations (cross-location) ──────────
class TestCheckD:
    def test_same_table_number_in_different_locations(
        self, db, restaurant, restaurant2, location, location2
    ):
        """D. table_number='D5' в Location1 и Location2 → оба должны пройти flush."""
        t1 = RestaurantTable(
            restaurant_id=restaurant.id,
            location_id=location.id,
            table_number="D5",
        )
        t2 = RestaurantTable(
            restaurant_id=restaurant2.id,
            location_id=location2.id,
            table_number="D5",
        )
        db.add_all([t1, t2])
        db.flush()  # must NOT raise

        assert t1.id is not None
        assert t2.id is not None
        assert t1.table_number == t2.table_number
        assert t1.location_id != t2.location_id


# ── J. Ключевой regression: два Location одного Brand — одинаковый номер → OK ──
class TestSameNumberDifferentLocations:
    def test_same_brand_two_locations_same_table_number(
        self, db, restaurant, location
    ):
        """
        J. REGRESSION: Brand A → Location A1 (table "5") + Location A2 (table "5")
        Должен PASS. Именно этот сценарий ломался при uq_table_restaurant_number.
        """
        # Создаём второй Location для того же ресторана (тот же Brand)
        loc2 = Location(
            restaurant_id=restaurant.id,
            name="Филиал 2",
            slug=f"{restaurant.slug}-branch2",
            is_active=True,
            timezone="Asia/Tashkent",
            delivery_fee=0,
            min_order_amount=0,
            currency="UZS",
            language="uz",
            is_waiter_call_enabled=False,
        )
        db.add(loc2)
        db.flush()

        # table fixture уже создал стол "5" в location (Location A1)
        # Создаём стол "5" в loc2 (Location A2) — тот же Brand, тот же номер
        t_loc2 = RestaurantTable(
            restaurant_id=restaurant.id,
            location_id=loc2.id,
            table_number="5",  # same as fixture table in location
        )
        db.add(t_loc2)
        db.flush()  # MUST NOT raise — разные Location, одинаковый номер OK

        assert t_loc2.id is not None
        assert t_loc2.table_number == "5"
        assert t_loc2.location_id == loc2.id
        assert t_loc2.restaurant_id == restaurant.id


# ── I. Дубль в одной Location → IntegrityError ────────────────────────────────
class TestDuplicateWithinLocation:
    def test_duplicate_table_number_same_location_raises(
        self, db, table, location, restaurant
    ):
        """I. Тот же table_number в той же Location → IntegrityError (409)."""
        t_dup = RestaurantTable(
            restaurant_id=restaurant.id,
            location_id=location.id,
            table_number=table.table_number,  # "5" — уже есть
        )
        db.add(t_dup)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


# ── E/F/G. Поля и relationship ─────────────────────────────────────────────────
class TestFields:
    def test_location_id_set(self, db, table, location):
        """E. location_id заполнен и корректен."""
        assert table.location_id == location.id

    def test_location_relationship(self, db, table, location):
        """F. location relationship работает."""
        db.refresh(table)
        assert table.location is not None
        assert table.location.id == location.id
        assert table.location.restaurant_id == table.restaurant_id

    def test_legacy_restaurant_id_present(self, db, table, restaurant):
        """G. Legacy restaurant_id не удалён."""
        assert table.restaurant_id == restaurant.id


# ── H. Модель содержит только uq_table_location_number ────────────────────────
class TestModelConstraints:
    def test_only_location_unique_constraint_in_model(self):
        """H. uq_table_restaurant_number удалён; uq_table_location_number есть."""
        names = {
            c.name
            for c in RestaurantTable.__table_args__
            if hasattr(c, "name")
        }
        assert "uq_table_location_number" in names, (
            "uq_table_location_number missing from model"
        )
        assert "uq_table_restaurant_number" not in names, (
            "uq_table_restaurant_number must be DROPPED — still present in model"
        )
