"""
tests/test_s1_4_location_id.py — S1-4: location_id на reservations, waiter_calls, usage_events

Доказанная причина 6 failures в CI (лог 2026-08-22):
  Все HTTP тесты CheckD/K/S/T возвращали 404 {"detail": "Not Found"}.
  Причина: тесты вызывали /v1/... вместо /api/...
  Доказательство: S1-3 тесты (PASS) используют /api/orders/ напрямую.
  TestClient вызывает app напрямую — versioning middleware не нужен в feature тестах.
  Исправление: /v1/reservations/ → /api/reservations/, /v1/waiter-calls/ → /api/waiter-calls/

Доказанные fixtures errors в test_tenant_isolation.py (лог 2026-08-22):
  NotNullViolation: location_id IS NULL в reservation_r2 и waiter_call_r2.
  Исправлено в test_tenant_isolation.py отдельно.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from models import Location, Reservation, RestaurantTable, UsageEvent, WaiterCall


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _seed_reservation(db, restaurant, location, **kwargs) -> Reservation:
    r = Reservation(
        restaurant_id=restaurant.id,
        location_id=location.id,
        client_name="Тест Тестов",
        client_phone="+998901234567",
        guests_count=2,
        reservation_time="2099-12-31T18:00:00+05:00",
        status="new",
        **kwargs,
    )
    db.add(r)
    db.flush()
    return r


def _seed_waiter_call(db, restaurant, location, table) -> WaiterCall:
    wc = WaiterCall(
        restaurant_id=restaurant.id,
        location_id=location.id,
        table_id=table.id,
        status="active",
    )
    db.add(wc)
    db.flush()
    return wc


def _seed_usage_event(db, restaurant, location=None, event_type="order_created") -> UsageEvent:
    ue = UsageEvent(
        restaurant_id=restaurant.id,
        location_id=location.id if location else None,
        event_type=event_type,
    )
    db.add(ue)
    db.flush()
    return ue


# ══════════════════════════════════════════════════════════════════════════════
# A. RESERVATIONS: location_id NOT NULL
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckA:
    def test_location_id_not_nullable_in_model(self):
        col = Reservation.__table__.c["location_id"]
        assert not col.nullable

    def test_no_null_location_id_after_seed(self, db, restaurant, location):
        _seed_reservation(db, restaurant, location)
        _seed_reservation(db, restaurant, location)
        null_count = db.query(Reservation).filter(Reservation.location_id.is_(None)).count()
        assert null_count == 0


# ══════════════════════════════════════════════════════════════════════════════
# B. RESERVATIONS: FK → locations.id ON DELETE RESTRICT
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckB:
    def test_location_id_has_fk(self):
        col = Reservation.__table__.c["location_id"]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "locations.id" in fk_targets

    def test_location_id_fk_on_delete_restrict(self):
        col = Reservation.__table__.c["location_id"]
        for fk in col.foreign_keys:
            if fk.target_fullname == "locations.id":
                assert fk.ondelete.upper() == "RESTRICT"


# ══════════════════════════════════════════════════════════════════════════════
# C. RESERVATIONS: cross-brand consistency
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckC:
    def test_cross_brand_consistency(self, db, restaurant, location):
        _seed_reservation(db, restaurant, location)
        bad = (
            db.query(Reservation)
            .join(Location, Location.id == Reservation.location_id)
            .filter(Reservation.restaurant_id != Location.restaurant_id)
            .all()
        )
        assert len(bad) == 0


# ══════════════════════════════════════════════════════════════════════════════
# D. RESERVATIONS: HTTP create — /api/reservations/ + X-Location-Id header
# client fixture (conftest) передаёт X-Location-Id: location.id по умолчанию.
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckD:
    def test_create_reservation_sets_location_id(self, client, db, restaurant, location):
        resp = client.post(
            "/api/reservations/",
            json={
                "client_name": "Алишер",
                "client_phone": "+998901234567",
                "guests_count": 2,
                "reservation_time": "2099-12-31T18:00:00+05:00",
            },
        )
        assert resp.status_code == 201, f"D FAIL: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data["location_id"] == location.id

        res = db.query(Reservation).filter(Reservation.id == data["id"]).first()
        assert res is not None
        assert res.location_id == location.id


# ══════════════════════════════════════════════════════════════════════════════
# E. RESERVATIONS: cross-brand injection → 404
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckE:
    def test_cross_brand_location_rejected(self, client, restaurant2, location2):
        resp = client.post(
            "/api/reservations/",
            json={
                "client_name": "Хакер",
                "client_phone": "+998901234567",
                "guests_count": 1,
                "reservation_time": "2099-12-31T18:00:00+05:00",
            },
            headers={"X-Location-Id": str(location2.id)},
        )
        assert resp.status_code == 404, f"E FAIL: got {resp.status_code}"


# ══════════════════════════════════════════════════════════════════════════════
# F. RESERVATIONS: inactive Location → 404
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckF:
    def test_inactive_location_rejected(self, client, db, restaurant):
        inactive_loc = Location(
            restaurant_id=restaurant.id,
            name="Закрытая точка",
            slug=f"{restaurant.slug}-closed",
            is_active=False,
            timezone="Asia/Tashkent",
            delivery_fee=0,
            min_order_amount=0,
            currency="UZS",
            language="uz",
            is_waiter_call_enabled=False,
        )
        db.add(inactive_loc)
        db.flush()

        resp = client.post(
            "/api/reservations/",
            json={
                "client_name": "Тест",
                "client_phone": "+998901234567",
                "guests_count": 1,
                "reservation_time": "2099-12-31T18:00:00+05:00",
            },
            headers={"X-Location-Id": str(inactive_loc.id)},
        )
        assert resp.status_code == 404, f"F FAIL: got {resp.status_code}"


# ══════════════════════════════════════════════════════════════════════════════
# G. RESERVATIONS: legacy restaurant_id присутствует
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckG:
    def test_legacy_restaurant_id_present(self):
        assert "restaurant_id" in Reservation.__table__.c

    def test_reservation_has_both_ids(self, db, restaurant, location):
        r = _seed_reservation(db, restaurant, location)
        assert r.restaurant_id == restaurant.id
        assert r.location_id == location.id


# ══════════════════════════════════════════════════════════════════════════════
# H. RESERVATIONS: FK RESTRICT блокирует удаление Location с бронями
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckH:
    def test_cannot_delete_location_with_reservations(self, db, restaurant, location):
        _seed_reservation(db, restaurant, location)
        db.flush()
        with pytest.raises(IntegrityError):
            db.delete(location)
            db.flush()


# ══════════════════════════════════════════════════════════════════════════════
# I. WAITER_CALLS: location_id NOT NULL
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckI:
    def test_location_id_not_nullable_in_model(self):
        col = WaiterCall.__table__.c["location_id"]
        assert not col.nullable

    def test_no_null_location_id_after_seed(self, db, restaurant, location, table):
        _seed_waiter_call(db, restaurant, location, table)
        null_count = db.query(WaiterCall).filter(WaiterCall.location_id.is_(None)).count()
        assert null_count == 0


# ══════════════════════════════════════════════════════════════════════════════
# J. WAITER_CALLS: FK → locations.id ON DELETE CASCADE
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckJ:
    def test_location_id_has_fk(self):
        col = WaiterCall.__table__.c["location_id"]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "locations.id" in fk_targets

    def test_location_id_fk_on_delete_cascade(self):
        col = WaiterCall.__table__.c["location_id"]
        for fk in col.foreign_keys:
            if fk.target_fullname == "locations.id":
                assert fk.ondelete.upper() == "CASCADE"


# ══════════════════════════════════════════════════════════════════════════════
# K. WAITER_CALLS: HTTP create — location_id из table.location_id
# /api/waiter-calls/ (доказано: /v1/ давало 404)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckK:
    def test_create_waiter_call_sets_location_id_from_table(
        self, client, db, restaurant, location, table
    ):
        resp = client.post("/api/waiter-calls/", json={"table_id": table.id})
        assert resp.status_code == 201, f"K FAIL: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data["location_id"] == table.location_id

        wc = db.query(WaiterCall).filter(WaiterCall.id == data["id"]).first()
        assert wc is not None
        assert wc.location_id == table.location_id


# ══════════════════════════════════════════════════════════════════════════════
# L. WAITER_CALLS: cross-brand consistency
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckL:
    def test_cross_brand_consistency(self, db, restaurant, location, table):
        _seed_waiter_call(db, restaurant, location, table)
        bad = (
            db.query(WaiterCall)
            .join(Location, Location.id == WaiterCall.location_id)
            .filter(WaiterCall.restaurant_id != Location.restaurant_id)
            .all()
        )
        assert len(bad) == 0


# ══════════════════════════════════════════════════════════════════════════════
# M. WAITER_CALLS: legacy restaurant_id присутствует
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckM:
    def test_legacy_restaurant_id_present(self):
        assert "restaurant_id" in WaiterCall.__table__.c

    def test_waiter_call_has_both_ids(self, db, restaurant, location, table):
        wc = _seed_waiter_call(db, restaurant, location, table)
        assert wc.restaurant_id == restaurant.id
        assert wc.location_id == location.id


# ══════════════════════════════════════════════════════════════════════════════
# N. USAGE_EVENTS: location_id nullable
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckN:
    def test_location_id_nullable_in_model(self):
        col = UsageEvent.__table__.c["location_id"]
        assert col.nullable

    def test_usage_event_without_location_id_ok(self, db, restaurant):
        ue = _seed_usage_event(db, restaurant, location=None)
        assert ue.id is not None
        assert ue.location_id is None


# ══════════════════════════════════════════════════════════════════════════════
# O. USAGE_EVENTS: FK → locations.id ON DELETE SET NULL
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckO:
    def test_location_id_has_fk(self):
        col = UsageEvent.__table__.c["location_id"]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "locations.id" in fk_targets

    def test_location_id_fk_on_delete_set_null(self):
        col = UsageEvent.__table__.c["location_id"]
        for fk in col.foreign_keys:
            if fk.target_fullname == "locations.id":
                assert fk.ondelete.upper() == "SET NULL"


# ══════════════════════════════════════════════════════════════════════════════
# P. USAGE_EVENTS: создаётся с location_id
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckP:
    def test_usage_event_with_location_id(self, db, restaurant, location):
        ue = _seed_usage_event(db, restaurant, location, event_type="order_created")
        assert ue.location_id == location.id

    def test_usage_event_location_matches_restaurant(self, db, restaurant, location):
        ue = _seed_usage_event(db, restaurant, location)
        loc = db.query(Location).filter(Location.id == ue.location_id).first()
        assert loc is not None
        assert loc.restaurant_id == ue.restaurant_id


# ══════════════════════════════════════════════════════════════════════════════
# Q. USAGE_EVENTS: NULL location_id не ломает исторические events
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckQ:
    def test_null_location_id_historical_event(self, db, restaurant):
        for event_type in ("order_created", "product_created", "product_deleted"):
            ue = _seed_usage_event(db, restaurant, location=None, event_type=event_type)
            assert ue.id is not None
            assert ue.location_id is None

    def test_mixed_null_and_set_location_ids(self, db, restaurant, location):
        ue_with = _seed_usage_event(db, restaurant, location)
        ue_without = _seed_usage_event(db, restaurant, location=None)
        assert ue_with.location_id == location.id
        assert ue_without.location_id is None
        total = db.query(UsageEvent).filter(UsageEvent.restaurant_id == restaurant.id).count()
        assert total >= 2


# ══════════════════════════════════════════════════════════════════════════════
# R. USAGE_EVENTS: cross-brand consistency (non-NULL)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckR:
    def test_cross_brand_consistency_non_null(self, db, restaurant, location):
        _seed_usage_event(db, restaurant, location)
        bad = (
            db.query(UsageEvent)
            .join(Location, Location.id == UsageEvent.location_id)
            .filter(UsageEvent.restaurant_id != Location.restaurant_id)
            .all()
        )
        assert len(bad) == 0


# ══════════════════════════════════════════════════════════════════════════════
# S. REGRESSION: GET reservations по restaurant_id (brand-level admin)
# /api/reservations/restaurant/{id} (доказано: /v1/ давало 404)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckS:
    def test_get_reservations_by_restaurant(self, client, db, restaurant, location):
        _seed_reservation(db, restaurant, location)
        _seed_reservation(db, restaurant, location)
        db.flush()

        resp = client.get(f"/api/reservations/restaurant/{restaurant.id}")
        assert resp.status_code == 200, f"S FAIL: {resp.status_code} {resp.text}"
        data = resp.json()
        assert len(data) >= 2
        for item in data:
            assert "location_id" in item

    def test_tenant_isolation_reservations(self, client, db, restaurant2, location2):
        r = Reservation(
            restaurant_id=restaurant2.id,
            location_id=location2.id,
            client_name="Чужой",
            client_phone="+998901234567",
            guests_count=1,
            reservation_time="2099-12-31T18:00:00+05:00",
            status="new",
        )
        db.add(r)
        db.flush()

        resp = client.get(f"/api/reservations/restaurant/{restaurant2.id}")
        assert resp.status_code == 403, f"S2 FAIL: got {resp.status_code}"


# ══════════════════════════════════════════════════════════════════════════════
# T. REGRESSION: WaiterCall race condition (дубль → 400)
# /api/waiter-calls/ (доказано: /v1/ давало 404)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckT:
    def test_duplicate_active_waiter_call_rejected(
        self, client, db, restaurant, location, table
    ):
        resp1 = client.post("/api/waiter-calls/", json={"table_id": table.id})
        assert resp1.status_code == 201, f"T FAIL: первый вызов: {resp1.status_code} {resp1.text}"

        resp2 = client.post("/api/waiter-calls/", json={"table_id": table.id})
        assert resp2.status_code == 400, f"T FAIL: дубль должен быть 400, got {resp2.status_code}"

    def test_waiter_call_location_id_from_table(
        self, client, db, restaurant, location, table
    ):
        resp = client.post("/api/waiter-calls/", json={"table_id": table.id})
        assert resp.status_code == 201, f"T2 FAIL: {resp.status_code} {resp.text}"
        assert resp.json()["location_id"] == table.location_id
