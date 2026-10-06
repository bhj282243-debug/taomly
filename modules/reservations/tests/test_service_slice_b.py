"""
modules/reservations/tests/test_service_slice_b.py — Phase 15, Slice B (R1), File #9

Тесты сервиса брони: статус requested, флаг is_reservation_enabled, idempotency
(Option B), replay и MC-07, доступ (RD-01), переходы статусов и lifecycle timestamps.
Не заменяют и не меняют набор MC-07 (test_guest_limits.py).
"""

from datetime import UTC, datetime, timedelta
import os
import threading
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from auth import TelegramUser, hash_password
from models import Agency, Location, Reservation, Restaurant
from modules.access import Principal, restaurant_admin_principal
from modules.reservations import service as res_service
from schemas import ReservationCreate

pytestmark = pytest.mark.postgres   # MC-09: как MC-07 — на SQLite-job пропускаются

PHONE = "+998901234567"
OTHER_PHONE = "+998933334455"
KEY = "idem-key-0001"
OTHER_KEY = "idem-key-0002"

# Фиксированное время брони: повтор обязан совпасть по reservation_time.
RT = datetime.now(UTC) + timedelta(days=1)


# ──────────────────────────────────────────
# helpers
# ──────────────────────────────────────────
def _tg(restaurant: Restaurant, tid: int = 0) -> TelegramUser:
    """tid=0 — Guest; tid>0 — Verified Telegram user."""
    return TelegramUser(
        id=tid, first_name="T", last_name=None, username=None, language_code="uz",
        restaurant_id=restaurant.id, restaurant=restaurant,
    )


def _data(phone: str = PHONE, **over) -> ReservationCreate:
    body = {
        "client_name": "Тест",
        "client_phone": phone,
        "guests_count": 2,
        "reservation_time": RT,
        "comment": None,
    }
    body.update(over)
    return ReservationCreate(**body)


def _create(db, restaurant, location, *, tid=0, key=None, data=None, now=None):
    return res_service.create_reservation_idempotent(
        db, tg_user=_tg(restaurant, tid), location_id=location.id,
        data=data or _data(), idempotency_key=key, now=now,
    )


def _add_row(db, location, *, status="requested", phone=PHONE, **extra) -> Reservation:
    row = Reservation(
        restaurant_id=location.restaurant_id, location_id=location.id,
        client_name="Fixture", client_phone=phone, guests_count=2,
        reservation_time=RT + timedelta(days=1), status=status, **extra,
    )
    db.add(row)
    db.flush()
    return row


def _count(db, location) -> int:
    return db.query(func.count(Reservation.id)).filter(
        Reservation.location_id == location.id
    ).scalar()


def _admin(restaurant) -> Principal:
    return restaurant_admin_principal(restaurant)


# ──────────────────────────────────────────
# Create: статус и флаг
# ──────────────────────────────────────────
def test_new_reservation_starts_as_requested(db, restaurant, location):
    guest = _create(db, restaurant, location)
    assert guest.reservation.status == "requested"
    assert guest.reservation.client_telegram_id is None
    assert guest.replayed is False

    verified = _create(db, restaurant, location, tid=777, data=_data(OTHER_PHONE))
    assert verified.reservation.status == "requested"
    assert verified.reservation.client_telegram_id == 777


def test_create_reservation_wrapper_returns_reservation(db, restaurant, location):
    reservation = res_service.create_reservation(
        db, tg_user=_tg(restaurant), location_id=location.id, data=_data()
    )
    assert isinstance(reservation, Reservation)
    assert reservation.status == "requested"


def test_disabled_flag_blocks_new_reservation_with_403(db, restaurant, location):
    location.is_reservation_enabled = False
    db.flush()
    for tid in (0, 777):
        with pytest.raises(HTTPException) as exc:
            _create(db, restaurant, location, tid=tid)
        assert exc.value.status_code == 403
    assert _count(db, location) == 0


def test_disabled_flag_does_not_block_existing_reservation_or_replay(db, restaurant, location):
    first = _create(db, restaurant, location, key=KEY)
    location.is_reservation_enabled = False
    db.flush()

    # существующая бронь по-прежнему управляется
    moved = res_service.transition_status(db, _admin(restaurant), first.reservation.id, "confirmed")
    assert moved.status == "confirmed"

    # replay не создаёт новую бронь и флагом не блокируется
    again = _create(db, restaurant, location, key=KEY)
    assert again.replayed is True and again.reservation.id == first.reservation.id
    assert _count(db, location) == 1


# ──────────────────────────────────────────
# Idempotency (Option B)
# ──────────────────────────────────────────
def test_exact_replay_returns_existing_reservation(db, restaurant, location):
    first = _create(db, restaurant, location, key=KEY)
    again = _create(db, restaurant, location, key=KEY)
    assert first.replayed is False and again.replayed is True
    assert again.reservation.id == first.reservation.id
    assert _count(db, location) == 1


@pytest.mark.parametrize(
    "case",
    ["phone", "name", "guests", "time", "comment", "telegram"],
)
def test_replay_mismatch_in_any_of_six_fields_returns_409(db, restaurant, location, case):
    first = _create(db, restaurant, location, key=KEY, tid=777)
    tid, data = 777, _data()
    if case == "phone":
        data = _data(OTHER_PHONE)
    elif case == "name":
        data = _data(client_name="Другой")
    elif case == "guests":
        data = _data(guests_count=3)
    elif case == "time":
        data = _data(reservation_time=RT + timedelta(hours=1))
    elif case == "comment":
        data = _data(comment="иначе")
    else:
        tid = 888
    with pytest.raises(HTTPException) as exc:
        _create(db, restaurant, location, key=KEY, tid=tid, data=data)
    assert exc.value.status_code == 409
    assert "Тест" not in str(exc.value.detail) and PHONE not in str(exc.value.detail)
    assert _count(db, location) == 1
    assert first.reservation.id is not None


def test_different_keys_create_separate_reservations(db, restaurant, location):
    a = _create(db, restaurant, location, key=KEY, tid=777)
    b = _create(db, restaurant, location, key=OTHER_KEY, tid=777)
    assert a.reservation.id != b.reservation.id
    assert a.replayed is False and b.replayed is False
    assert _count(db, location) == 2


def test_null_key_creates_normally_each_time(db, restaurant, location):
    a = _create(db, restaurant, location, tid=777)
    b = _create(db, restaurant, location, tid=777)
    assert a.reservation.id != b.reservation.id
    assert a.replayed is False and b.replayed is False
    assert a.reservation.idempotency_key is None
    assert _count(db, location) == 2


def test_replay_does_not_hit_or_consume_mc07_limits(db, restaurant, location):
    first = _create(db, restaurant, location, key=KEY)          # Guest, активная №1
    _add_row(db, location)
    _add_row(db, location)                                        # активных 3 -> новая = 409
    with pytest.raises(HTTPException) as active:
        _create(db, restaurant, location, key=OTHER_KEY)
    assert active.value.status_code == 409

    again = _create(db, restaurant, location, key=KEY)           # replay: без 409
    assert again.replayed is True and again.reservation.id == first.reservation.id

    _add_row(db, location, status="completed")
    _add_row(db, location, status="completed")                    # создано за час: 5 -> новая = 429
    with pytest.raises(HTTPException) as created:
        _create(db, restaurant, location, key=OTHER_KEY)
    assert created.value.status_code == 429

    again = _create(db, restaurant, location, key=KEY)           # replay: без 429
    assert again.replayed is True and again.reservation.id == first.reservation.id


# ──────────────────────────────────────────
# Доступ (RD-01): через transition_status
# ──────────────────────────────────────────
def test_access_scope_none_and_matching_scope_allowed(db, restaurant, location):
    row = _add_row(db, location)
    ok = res_service.transition_status(db, _admin(restaurant), row.id, "confirmed")
    assert ok.status == "confirmed"

    row2 = _add_row(db, location)
    scoped = Principal(restaurant, frozenset({location.id}), "restaurant_admin:test")
    assert res_service.transition_status(db, scoped, row2.id, "confirmed").status == "confirmed"


def test_access_out_of_scope_location_is_403(db, restaurant, location):
    row = _add_row(db, location)
    scoped = Principal(restaurant, frozenset({location.id + 1000}), "restaurant_admin:test")
    with pytest.raises(HTTPException) as exc:
        res_service.transition_status(db, scoped, row.id, "confirmed")
    assert exc.value.status_code == 403
    assert row.status == "requested"


def test_access_foreign_restaurant_reservation_is_404(db, restaurant, restaurant2, location):
    row = _add_row(db, location)
    with pytest.raises(HTTPException) as exc:
        res_service.transition_status(db, _admin(restaurant2), row.id, "confirmed")
    assert exc.value.status_code == 404


def test_create_with_location_of_foreign_restaurant_is_404(db, restaurant, restaurant2):
    foreign = Location(
        restaurant_id=restaurant2.id, name="foreign", slug="foreign-loc", is_active=True,
        timezone="Asia/Tashkent", delivery_fee=0, min_order_amount=0, currency="UZS",
        language="uz", is_waiter_call_enabled=False,
    )
    db.add(foreign)
    db.flush()
    with pytest.raises(HTTPException) as exc:
        _create(db, restaurant, foreign)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Location не найдена или недоступна для этого ресторана"


# ──────────────────────────────────────────
# Статусы и lifecycle timestamps
# ──────────────────────────────────────────
@pytest.mark.parametrize(
    ("start", "target", "field"),
    [
        ("requested", "confirmed", "confirmed_at"),
        ("requested", "cancelled", "cancelled_at"),
        ("confirmed", "seated", "seated_at"),
        ("confirmed", "completed", "completed_at"),
        ("confirmed", "cancelled", "cancelled_at"),
        ("confirmed", "no_show", "no_show_at"),
        ("seated", "completed", "completed_at"),
        ("new", "confirmed", "confirmed_at"),    # R1: legacy 'new' ведёт себя как requested
        ("new", "cancelled", "cancelled_at"),
    ],
)
def test_valid_transitions_set_only_their_timestamp(db, restaurant, location, start, target, field):
    row = _add_row(db, location, status=start)
    moment = datetime.now(UTC).replace(microsecond=0)
    moved = res_service.transition_status(db, _admin(restaurant), row.id, target, now=moment)
    assert moved.status == target
    assert getattr(moved, field) == moment
    others = {
        "confirmed_at", "seated_at", "completed_at", "cancelled_at", "no_show_at"
    } - {field}
    assert all(getattr(moved, name) is None for name in others)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("requested", "seated"), ("requested", "completed"), ("requested", "no_show"),
        ("requested", "requested"), ("requested", "new"),
        ("confirmed", "requested"), ("confirmed", "confirmed"),
        ("seated", "confirmed"), ("seated", "cancelled"), ("seated", "no_show"),
        ("completed", "confirmed"), ("completed", "cancelled"),
        ("cancelled", "confirmed"), ("cancelled", "seated"),
        ("no_show", "seated"), ("no_show", "completed"),
        ("new", "seated"), ("new", "completed"), ("new", "no_show"), ("new", "requested"),
    ],
)
def test_invalid_transitions_are_rejected_with_409(db, restaurant, location, start, target):
    row = _add_row(db, location, status=start)
    with pytest.raises(HTTPException) as exc:
        res_service.transition_status(db, _admin(restaurant), row.id, target)
    assert exc.value.status_code == 409
    assert row.status == start
    assert row.confirmed_at is row.seated_at is row.completed_at is None
    assert row.cancelled_at is row.no_show_at is None


def test_full_lifecycle_and_timestamps_are_not_overwritten(db, restaurant, location):
    admin = _admin(restaurant)
    t1, t2, t3 = (datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=i) for i in (1, 2, 3))
    created = _create(db, restaurant, location, tid=777).reservation
    res_service.transition_status(db, admin, created.id, "confirmed", now=t1)
    res_service.transition_status(db, admin, created.id, "seated", now=t2)
    done = res_service.transition_status(db, admin, created.id, "completed", now=t3)
    assert (done.confirmed_at, done.seated_at, done.completed_at) == (t1, t2, t3)
    assert done.cancelled_at is None and done.no_show_at is None

    # write-once: уже выставленное значение не перезаписывается
    preset = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    row = _add_row(db, location, status="seated", completed_at=preset)
    moved = res_service.transition_status(db, admin, row.id, "completed", now=t3)
    assert moved.completed_at == preset

    # терминальный статус: повторный переход невозможен, timestamp не меняется
    with pytest.raises(HTTPException) as exc:
        res_service.transition_status(db, admin, created.id, "completed", now=t1)
    assert exc.value.status_code == 409
    assert created.completed_at == t3


# ──────────────────────────────────────────
# Параллельные запросы: один и тот же (location_id, Idempotency-Key)
# ──────────────────────────────────────────
class _PgWorld:
    """Закоммиченные данные для параллельных соединений + гарантированная очистка."""

    def __init__(self):
        self.engine = create_engine(os.environ["DATABASE_URL"])
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        suffix = uuid.uuid4().hex[:10]
        s = self.Session()
        try:
            agency = Agency(
                name=f"sb-{suffix}", owner_email=f"sb-{suffix}@test.uz",
                owner_password_hash=hash_password("password123"),
            )
            s.add(agency)
            s.flush()
            restaurant = Restaurant(
                agency_id=agency.id, name=f"sb-{suffix}", slug=f"sb-{suffix}",
                admin_password_hash=hash_password("secret"),
                primary_color="#8B1A2E", secondary_color="#FAF6EE", accent_color="#D4A853",
                currency="UZS",
            )
            s.add(restaurant)
            s.flush()
            location = Location(
                restaurant_id=restaurant.id, name="sb", slug=f"sb-{suffix}",
                is_active=True, timezone="Asia/Tashkent", delivery_fee=0,
                min_order_amount=0, currency="UZS", language="uz",
                is_waiter_call_enabled=False,
            )
            s.add(location)
            s.flush()
            self.agency_id, self.restaurant_id, self.location_id = (
                agency.id, restaurant.id, location.id,
            )
            s.commit()
        finally:
            s.close()

    def attempt(self, tid, key, results, barrier):
        s = self.Session()
        try:
            restaurant = s.get(Restaurant, self.restaurant_id)
            data = _data()
            barrier.wait(timeout=15)
            try:
                out = res_service.create_reservation_idempotent(
                    s, tg_user=_tg(restaurant, tid), location_id=self.location_id,
                    data=data, idempotency_key=key,
                )
                results.append((tid, "replay" if out.replayed else "created", out.reservation.id))
            except HTTPException as exc:
                results.append((tid, exc.status_code, None))
        finally:
            s.close()

    def keyed_count(self, key) -> int:
        s = self.Session()
        try:
            return s.query(func.count(Reservation.id)).filter(
                Reservation.location_id == self.location_id,
                Reservation.idempotency_key == key,
            ).scalar()
        finally:
            s.close()

    def cleanup(self):
        s = self.Session()
        try:
            s.query(Reservation).filter(Reservation.restaurant_id == self.restaurant_id).delete()
            s.query(Location).filter(Location.id == self.location_id).delete()
            s.query(Restaurant).filter(Restaurant.id == self.restaurant_id).delete()
            s.query(Agency).filter(Agency.id == self.agency_id).delete()
            s.commit()
        finally:
            s.close()
            self.engine.dispose()


@pytest.fixture
def pg_world():
    world = _PgWorld()
    try:
        yield world
    finally:
        world.cleanup()


def _run_parallel(world, tids, key):
    results: list = []
    barrier = threading.Barrier(len(tids))
    threads = [
        threading.Thread(target=world.attempt, args=(tid, key, results, barrier)) for tid in tids
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_concurrent_same_key_creates_at_most_one_reservation(pg_world):
    results = _run_parallel(pg_world, [777] * 6, KEY)

    assert len(results) == 6
    assert pg_world.keyed_count(KEY) == 1
    created = [r for r in results if r[1] == "created"]
    replays = [r for r in results if r[1] == "replay"]
    assert len(created) == 1 and len(replays) == 5
    assert {r[2] for r in results} == {created[0][2]}


def test_concurrent_same_key_different_identity_never_creates_second(pg_world):
    results = _run_parallel(pg_world, [777, 777, 888, 888], KEY)

    assert len(results) == 4
    assert pg_world.keyed_count(KEY) == 1
    created = [r for r in results if r[1] == "created"]
    assert len(created) == 1
    winner = created[0][0]
    for tid, outcome, _ in results:
        if outcome == "created":
            continue
        # та же identity -> replay; другая identity -> 409, данные брони не раскрываются
        assert outcome == ("replay" if tid == winner else 409)
