"""
tests/test_s1_4_location_id.py — S1-4: location_id на reservations, waiter_calls, usage_events

Проверки:

  ── RESERVATIONS ──
  A. location_id NOT NULL в модели
  B. location_id FK указывает на locations.id
  C. Cross-brand consistency: reservation.restaurant_id == location.restaurant_id
  D. Reservation создаётся с location_id (через HTTP + X-Location-Id header)
  E. Cross-brand injection: X-Location-Id чужого бренда → 404
  F. Inactive Location → 404
  G. Legacy: restaurant_id присутствует (не удалён)
  H. FK RESTRICT: Location с бронями нельзя удалить (IntegrityError)

  ── WAITER_CALLS ──
  I.  location_id NOT NULL в модели
  J.  location_id FK указывает на locations.id
  K.  WaiterCall создаётся с location_id = table.location_id (HTTP)
  L.  Cross-brand consistency: waiter_call.restaurant_id == location.restaurant_id
  M.  Legacy: restaurant_id присутствует (не удалён)

  ── USAGE_EVENTS ──
  N.  location_id nullable в модели
  O.  location_id FK указывает на locations.id (или None)
  P.  UsageEvent создаётся с location_id в DB-уровне
  Q.  UsageEvent без location_id (NULL) не вызывает ошибки
  R.  Cross-brand consistency (для заполненных location_id)

  ── REGRESSION ──
  S.  Брони фильтруются по restaurant_id (brand-level admin — без изменений)
  T.  WaiterCall Race condition: дубль активного вызова → 400 (without regression)
"""

import pytest
from sqlalchemy.exc import IntegrityError

from models import Location, Reservation, RestaurantTable, UsageEvent, WaiterCall


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _seed_reservation(db, restaurant, location, **kwargs) -> Reservation:
    """Создаёт Reservation напрямую в БД."""
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
    """Создаёт WaiterCall напрямую в БД."""
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
    """Создаёт UsageEvent напрямую в БД. location_id nullable."""
    ue = UsageEvent(
        restaurant_id=restaurant.id,
        location_id=location.id if location else None,
        event_type=event_type,
    )
    db.add(ue)
    db.flush()
    return ue


# ══════════════════════════════════════════════════════════════════════════════
# A. RESERVATIONS: location_id NOT NULL в модели
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckA:
    def test_location_id_not_nullable_in_model(self):
        """A. Reservation.location_id определена как NOT NULL."""
        col = Reservation.__table__.c["location_id"]
        assert not col.nullable, "A FAIL: Reservation.location_id должна быть NOT NULL"

    def test_no_null_location_id_after_seed(self, db, restaurant, location):
        """A2. Все созданные Reservations имеют location_id."""
        _seed_reservation(db, restaurant, location)
        _seed_reservation(db, restaurant, location)

        null_count = (
            db.query(Reservation)
            .filter(Reservation.location_id.is_(None))
            .count()
        )
        assert null_count == 0, f"A2 FAIL: {null_count} reservations have location_id IS NULL"


# ══════════════════════════════════════════════════════════════════════════════
# B. RESERVATIONS: FK → locations.id
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckB:
    def test_location_id_has_fk(self):
        """B. Reservation.location_id имеет FK на locations.id."""
        col = Reservation.__table__.c["location_id"]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "locations.id" in fk_targets, (
            f"B FAIL: Reservation.location_id FK не указывает на locations.id. Got: {fk_targets}"
        )

    def test_location_id_fk_on_delete_restrict(self):
        """B2. FK RESTRICT: on_delete == RESTRICT."""
        col = Reservation.__table__.c["location_id"]
        for fk in col.foreign_keys:
            if fk.target_fullname == "locations.id":
                assert fk.ondelete.upper() == "RESTRICT", (
                    f"B2 FAIL: expected RESTRICT, got {fk.ondelete}"
                )


# ══════════════════════════════════════════════════════════════════════════════
# C. RESERVATIONS: cross-brand consistency
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckC:
    def test_cross_brand_consistency(self, db, restaurant, location):
        """C. Все Reservations имеют matching restaurant_id / location.restaurant_id."""
        _seed_reservation(db, restaurant, location)

        bad = (
            db.query(Reservation)
            .join(Location, Location.id == Reservation.location_id)
            .filter(Reservation.restaurant_id != Location.restaurant_id)
            .all()
        )
        assert len(bad) == 0, f"C FAIL: {len(bad)} cross-brand reservation rows"


# ══════════════════════════════════════════════════════════════════════════════
# D. RESERVATIONS: HTTP create с X-Location-Id
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckD:
    def test_create_reservation_sets_location_id(self, client, db, restaurant, location):
        """D. POST /reservations/ создаёт бронь с location_id."""
        resp = client.post(
            "/v1/reservations/",
            json={
                "client_name": "Алишер",
                "client_phone": "+998901234567",
                "guests_count": 2,
                "reservation_time": "2099-12-31T18:00:00+05:00",
            },
            headers={"X-Location-Id": str(location.id)},
        )
        assert resp.status_code == 201, f"D FAIL: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data["location_id"] == location.id, (
            f"D FAIL: expected location_id={location.id}, got {data.get('location_id')}"
        )

        # Verify in DB
        res = db.query(Reservation).filter(Reservation.id == data["id"]).first()
        assert res is not None
        assert res.location_id == location.id


# ══════════════════════════════════════════════════════════════════════════════
# E. RESERVATIONS: cross-brand injection → 404
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckE:
    def test_cross_brand_location_rejected(self, client, restaurant2, location2):
        """E. X-Location-Id чужого бренда → 404."""
        resp = client.post(
            "/v1/reservations/",
            json={
                "client_name": "Хакер",
                "client_phone": "+998901234567",
                "guests_count": 1,
                "reservation_time": "2099-12-31T18:00:00+05:00",
            },
            headers={"X-Location-Id": str(location2.id)},
        )
        assert resp.status_code == 404, (
            f"E FAIL: cross-brand Location должна давать 404, got {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# F. RESERVATIONS: inactive Location → 404
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckF:
    def test_inactive_location_rejected(self, client, db, restaurant):
        """F. Inactive Location → 404."""
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
            "/v1/reservations/",
            json={
                "client_name": "Тест",
                "client_phone": "+998901234567",
                "guests_count": 1,
                "reservation_time": "2099-12-31T18:00:00+05:00",
            },
            headers={"X-Location-Id": str(inactive_loc.id)},
        )
        assert resp.status_code == 404, (
            f"F FAIL: inactive Location должна давать 404, got {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# G. RESERVATIONS: legacy restaurant_id присутствует
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckG:
    def test_legacy_restaurant_id_present(self):
        """G. Reservation.restaurant_id не удалён (legacy column присутствует)."""
        assert "restaurant_id" in Reservation.__table__.c, (
            "G FAIL: Reservation.restaurant_id удалён — это нарушение Stage 1 constraint"
        )

    def test_reservation_has_both_ids(self, db, restaurant, location):
        """G2. Созданная Reservation имеет и restaurant_id, и location_id."""
        r = _seed_reservation(db, restaurant, location)
        assert r.restaurant_id == restaurant.id
        assert r.location_id == location.id


# ══════════════════════════════════════════════════════════════════════════════
# H. RESERVATIONS: FK RESTRICT — Location с бронями нельзя удалить
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckH:
    def test_cannot_delete_location_with_reservations(self, db, restaurant, location):
        """H. FK RESTRICT: попытка удалить Location с бронями → IntegrityError."""
        _seed_reservation(db, restaurant, location)
        db.flush()

        with pytest.raises(IntegrityError):
            db.delete(location)
            db.flush()


# ══════════════════════════════════════════════════════════════════════════════
# I. WAITER_CALLS: location_id NOT NULL в модели
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckI:
    def test_location_id_not_nullable_in_model(self):
        """I. WaiterCall.location_id определена как NOT NULL."""
        col = WaiterCall.__table__.c["location_id"]
        assert not col.nullable, "I FAIL: WaiterCall.location_id должна быть NOT NULL"

    def test_no_null_location_id_after_seed(self, db, restaurant, location, table):
        """I2. Созданные WaiterCalls имеют location_id."""
        _seed_waiter_call(db, restaurant, location, table)

        null_count = (
            db.query(WaiterCall)
            .filter(WaiterCall.location_id.is_(None))
            .count()
        )
        assert null_count == 0, f"I2 FAIL: {null_count} waiter_calls have location_id IS NULL"


# ══════════════════════════════════════════════════════════════════════════════
# J. WAITER_CALLS: FK → locations.id с ON DELETE CASCADE
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckJ:
    def test_location_id_has_fk(self):
        """J. WaiterCall.location_id имеет FK на locations.id."""
        col = WaiterCall.__table__.c["location_id"]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "locations.id" in fk_targets, (
            f"J FAIL: WaiterCall.location_id FK не указывает на locations.id. Got: {fk_targets}"
        )

    def test_location_id_fk_on_delete_cascade(self):
        """J2. FK CASCADE: on_delete == CASCADE."""
        col = WaiterCall.__table__.c["location_id"]
        for fk in col.foreign_keys:
            if fk.target_fullname == "locations.id":
                assert fk.ondelete.upper() == "CASCADE", (
                    f"J2 FAIL: expected CASCADE, got {fk.ondelete}"
                )


# ══════════════════════════════════════════════════════════════════════════════
# K. WAITER_CALLS: HTTP create берёт location_id из table.location_id
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckK:
    def test_create_waiter_call_sets_location_id_from_table(
        self, client, db, restaurant, location, table
    ):
        """K. POST /waiter-calls/ создаёт WaiterCall с location_id = table.location_id."""
        resp = client.post(
            "/v1/waiter-calls/",
            json={"table_id": table.id},
        )
        assert resp.status_code == 201, f"K FAIL: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data["location_id"] == table.location_id, (
            f"K FAIL: expected location_id={table.location_id}, got {data.get('location_id')}"
        )

        # Verify in DB
        wc = db.query(WaiterCall).filter(WaiterCall.id == data["id"]).first()
        assert wc is not None
        assert wc.location_id == table.location_id


# ══════════════════════════════════════════════════════════════════════════════
# L. WAITER_CALLS: cross-brand consistency
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckL:
    def test_cross_brand_consistency(self, db, restaurant, location, table):
        """L. Все WaiterCalls имеют matching restaurant_id / location.restaurant_id."""
        _seed_waiter_call(db, restaurant, location, table)

        bad = (
            db.query(WaiterCall)
            .join(Location, Location.id == WaiterCall.location_id)
            .filter(WaiterCall.restaurant_id != Location.restaurant_id)
            .all()
        )
        assert len(bad) == 0, f"L FAIL: {len(bad)} cross-brand waiter_call rows"


# ══════════════════════════════════════════════════════════════════════════════
# M. WAITER_CALLS: legacy restaurant_id присутствует
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckM:
    def test_legacy_restaurant_id_present(self):
        """M. WaiterCall.restaurant_id не удалён."""
        assert "restaurant_id" in WaiterCall.__table__.c, (
            "M FAIL: WaiterCall.restaurant_id удалён — нарушение Stage 1 constraint"
        )

    def test_waiter_call_has_both_ids(self, db, restaurant, location, table):
        """M2. WaiterCall имеет и restaurant_id, и location_id."""
        wc = _seed_waiter_call(db, restaurant, location, table)
        assert wc.restaurant_id == restaurant.id
        assert wc.location_id == location.id


# ══════════════════════════════════════════════════════════════════════════════
# N. USAGE_EVENTS: location_id nullable
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckN:
    def test_location_id_nullable_in_model(self):
        """N. UsageEvent.location_id определена как nullable (не NOT NULL)."""
        col = UsageEvent.__table__.c["location_id"]
        assert col.nullable, "N FAIL: UsageEvent.location_id должна быть nullable"

    def test_usage_event_without_location_id_ok(self, db, restaurant):
        """N2. UsageEvent с location_id=NULL не вызывает ошибок."""
        ue = _seed_usage_event(db, restaurant, location=None)
        assert ue.id is not None
        assert ue.location_id is None


# ══════════════════════════════════════════════════════════════════════════════
# O. USAGE_EVENTS: FK → locations.id (SET NULL)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckO:
    def test_location_id_has_fk(self):
        """O. UsageEvent.location_id имеет FK на locations.id."""
        col = UsageEvent.__table__.c["location_id"]
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "locations.id" in fk_targets, (
            f"O FAIL: UsageEvent.location_id FK не указывает на locations.id. Got: {fk_targets}"
        )

    def test_location_id_fk_on_delete_set_null(self):
        """O2. FK SET NULL: on_delete == SET NULL."""
        col = UsageEvent.__table__.c["location_id"]
        for fk in col.foreign_keys:
            if fk.target_fullname == "locations.id":
                assert fk.ondelete.upper() == "SET NULL", (
                    f"O2 FAIL: expected SET NULL, got {fk.ondelete}"
                )


# ══════════════════════════════════════════════════════════════════════════════
# P. USAGE_EVENTS: создаётся с location_id в DB-уровне
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckP:
    def test_usage_event_with_location_id(self, db, restaurant, location):
        """P. UsageEvent с location_id сохраняется корректно."""
        ue = _seed_usage_event(db, restaurant, location, event_type="order_created")
        assert ue.location_id == location.id

    def test_usage_event_location_matches_restaurant(self, db, restaurant, location):
        """P2. UsageEvent: location.restaurant_id == usage_event.restaurant_id."""
        ue = _seed_usage_event(db, restaurant, location)
        loc = db.query(Location).filter(Location.id == ue.location_id).first()
        assert loc is not None
        assert loc.restaurant_id == ue.restaurant_id, (
            f"P2 FAIL: cross-brand: ue.restaurant_id={ue.restaurant_id}, "
            f"loc.restaurant_id={loc.restaurant_id}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Q. USAGE_EVENTS: NULL location_id не ломает исторические events
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckQ:
    def test_null_location_id_historical_event(self, db, restaurant):
        """Q. Исторический UsageEvent (location_id=NULL) сохраняется без ошибок."""
        for event_type in ("order_created", "product_created", "product_deleted"):
            ue = _seed_usage_event(db, restaurant, location=None, event_type=event_type)
            assert ue.id is not None
            assert ue.location_id is None
            assert ue.restaurant_id == restaurant.id

    def test_mixed_null_and_set_location_ids(self, db, restaurant, location):
        """Q2. Mix NULL и non-NULL location_id в одной таблице — оба работают."""
        ue_with = _seed_usage_event(db, restaurant, location)
        ue_without = _seed_usage_event(db, restaurant, location=None)

        assert ue_with.location_id == location.id
        assert ue_without.location_id is None

        total = db.query(UsageEvent).filter(
            UsageEvent.restaurant_id == restaurant.id
        ).count()
        assert total >= 2


# ══════════════════════════════════════════════════════════════════════════════
# R. USAGE_EVENTS: cross-brand consistency (для non-NULL rows)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckR:
    def test_cross_brand_consistency_non_null(self, db, restaurant, location):
        """R. Заполненные location_id в usage_events соответствуют restaurant_id."""
        _seed_usage_event(db, restaurant, location)

        bad = (
            db.query(UsageEvent)
            .join(Location, Location.id == UsageEvent.location_id)
            .filter(UsageEvent.restaurant_id != Location.restaurant_id)
            .all()
        )
        assert len(bad) == 0, f"R FAIL: {len(bad)} cross-brand usage_event rows"


# ══════════════════════════════════════════════════════════════════════════════
# S. REGRESSION: брони по restaurant_id (brand-level admin — без изменений)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckS:
    def test_get_reservations_by_restaurant(self, client, db, restaurant, location):
        """S. GET /reservations/restaurant/{id} возвращает брони бренда (regression)."""
        _seed_reservation(db, restaurant, location)
        _seed_reservation(db, restaurant, location)
        db.flush()

        resp = client.get(f"/v1/reservations/restaurant/{restaurant.id}")
        assert resp.status_code == 200, f"S FAIL: {resp.status_code} {resp.text}"
        data = resp.json()
        assert len(data) >= 2, f"S FAIL: expected >= 2 reservations, got {len(data)}"

        # Все брони имеют location_id
        for item in data:
            assert "location_id" in item, "S FAIL: location_id absent in response"

    def test_tenant_isolation_reservations(
        self, client, db, restaurant, location, restaurant2, location2
    ):
        """S2. Брони другого бренда не видны через JWT ресторана A."""
        _seed_reservation(db, restaurant2, location2)
        db.flush()

        resp = client.get(f"/v1/reservations/restaurant/{restaurant2.id}")
        assert resp.status_code == 403, (
            f"S2 FAIL: tenant isolation нарушена — got {resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T. REGRESSION: WaiterCall race condition (дубль → 400)
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckT:
    def test_duplicate_active_waiter_call_rejected(self, client, db, restaurant, location, table):
        """T. Дублирующий активный вызов того же стола → 400 (regression)."""
        # Первый вызов
        resp1 = client.post("/v1/waiter-calls/", json={"table_id": table.id})
        assert resp1.status_code == 201, f"T FAIL: первый вызов: {resp1.status_code}"

        # Второй вызов — дубль
        resp2 = client.post("/v1/waiter-calls/", json={"table_id": table.id})
        assert resp2.status_code == 400, (
            f"T FAIL: дублирующий вызов должен возвращать 400, got {resp2.status_code}"
        )

    def test_waiter_call_location_id_from_table(self, client, db, restaurant, location, table):
        """T2. location_id в ответе совпадает с table.location_id."""
        resp = client.post("/v1/waiter-calls/", json={"table_id": table.id})
        assert resp.status_code == 201
        assert resp.json()["location_id"] == table.location_id
