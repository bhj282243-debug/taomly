"""
Phase 15 — MC-07: гостевые лимиты бронирования (Spec v3.2: §14.2, RS-13, G-15).

Правила под тестом (для Guest-запроса, пара = нормализованный телефон + Location):
  * не более 5 созданных броней за последние 60 минут  → 429
  * не более 3 активных броней (requested/confirmed)    → 409
  * подсчёт по ВСЕМ броням пары (Guest и Verified), лимит — только для Guest
  * Verified Telegram user лимитам не подчиняется; IP-лимит 10/мин сохраняется
  * подсчёт в БД под pg_advisory_xact_lock; Redis и таблицы счётчиков нет

Примечания по окружению:
  * В БД сейчас существует статус 'new' (прежнее имя 'requested', OD-01); значение
    'requested' CHECK-констрейнт пока не допускает, поэтому «активные» брони
    создаются со статусами 'new' и 'confirmed'. 'seated'/'no_show' в БД ещё нет.
  * Тесты с @pytest.mark.postgres выполняются только на PostgreSQL
    (advisory lock и параллельные соединения на SQLite недоступны).
  * IP-лимит slowapi — 10/мин с одного IP; тесты, которым нужно больше запросов,
    готовят состояние прямыми INSERT и делают один POST.
"""

from datetime import UTC, datetime, timedelta
import os
import threading
import time
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker

from api import app
from auth import TelegramUser, get_telegram_user, hash_password
from config import settings
from models import Agency, Location, Reservation, Restaurant
from models.operations import phone_digits
from modules.reservations import service as res_service
from schemas import ReservationCreate

pytestmark = pytest.mark.postgres   # MC-09: на SQLite-job пропускаются (не добавляют errors к baseline)

PHONE = "+998901234567"
OTHER_PHONE = "+998933334455"


# ──────────────────────────────────────────
# helpers
# ──────────────────────────────────────────
def _future() -> datetime:
    return datetime.now(UTC) + timedelta(days=1)


def _payload(phone: str = PHONE, **over) -> dict:
    body = {
        "client_name": "Тест Гость",
        "client_phone": phone,
        "guests_count": 2,
        "reservation_time": _future().isoformat(),
        "comment": None,
    }
    body.update(over)
    return body


def _guest(restaurant: Restaurant) -> TelegramUser:
    return TelegramUser(
        id=0,
        first_name="Guest",
        last_name=None,
        username=None,
        language_code="uz",
        restaurant_id=restaurant.id,
        restaurant=restaurant,
    )


def _as_guest(restaurant: Restaurant) -> None:
    """Подменяет зависимость клиента на гостя (initData отсутствует)."""
    app.dependency_overrides[get_telegram_user] = lambda: _guest(restaurant)


def _add(db, location, *, phone=PHONE, status="new", age=timedelta(0), now=None) -> Reservation:
    """Прямой INSERT брони с заданным возрастом (created_at = now - age)."""
    created = (now or datetime.now(UTC)) - age
    row = Reservation(
        restaurant_id=location.restaurant_id,
        location_id=location.id,
        client_name="Fixture",
        client_phone=phone,
        guests_count=2,
        reservation_time=_future() + timedelta(days=1),
        status=status,
        created_at=created,
    )
    db.add(row)
    db.flush()
    return row


def _count(db, location, digits: str | None = None) -> int:
    q = db.query(func.count(Reservation.id)).filter(Reservation.location_id == location.id)
    if digits is not None:
        q = q.filter(Reservation.client_phone_digits == digits)
    return q.scalar()


def _post(client, location, phone: str = PHONE, **over):
    return client.post(
        "/api/reservations/",
        json=_payload(phone, **over),
        headers={"X-Location-Id": str(location.id)},
    )


def _service_create(db, restaurant, location, phone: str = PHONE, now=None):
    data = ReservationCreate(
        client_name="Тест", client_phone=phone, guests_count=2, reservation_time=_future()
    )
    return res_service.create_reservation(
        db, tg_user=_guest(restaurant), location_id=location.id, data=data, now=now
    )


# ──────────────────────────────────────────
# RS-13 (a): 5 созданных за 60 минут → 429
# ──────────────────────────────────────────
def test_rs13_a_sixth_created_within_60_min_returns_429(db, client, restaurant, location):
    _as_guest(restaurant)
    for _ in range(5):
        _add(db, location, status="completed", age=timedelta(minutes=10))

    r = _post(client, location)

    assert r.status_code == 429
    assert "броней" in r.json()["detail"]
    assert _count(db, location) == 5  # отклонённый запрос бронь не создал


def test_rs13_a_fifth_created_passes_then_sixth_is_429(db, client, restaurant, location):
    _as_guest(restaurant)
    for _ in range(4):
        _add(db, location, status="completed", age=timedelta(minutes=10))

    first = _post(client, location)
    second = _post(client, location)

    assert first.status_code == 201
    assert second.status_code == 429


# ──────────────────────────────────────────
# RS-13 (b): отменённые входят в счёт 60 минут
# ──────────────────────────────────────────
def test_rs13_b_cancelled_reservations_count_in_60_min_window(db, client, restaurant, location):
    _as_guest(restaurant)
    for _ in range(5):
        _add(db, location, status="cancelled", age=timedelta(minutes=5))

    assert _post(client, location).status_code == 429


# ──────────────────────────────────────────
# RS-13 (c): отклонённые запросы в счёт не входят
# ──────────────────────────────────────────
def test_rs13_c_rejected_requests_are_not_counted(db, client, restaurant, location, location2):
    _as_guest(restaurant)

    invalid = _post(client, location, guests_count=0)                      # 422
    missing = client.post(                                                  # 404 нет Location
        "/api/reservations/", json=_payload(), headers={"X-Location-Id": "999999"}
    )
    foreign = _post(client, location2)                                      # 404 чужой ресторан

    assert (invalid.status_code, missing.status_code, foreign.status_code) == (422, 404, 404)
    assert _count(db, location) == 0 and _count(db, location2) == 0

    # Отклонённые лимитом запросы тоже не создают строк.
    for _ in range(5):
        _add(db, location, status="completed", age=timedelta(minutes=1))
    assert _post(client, location).status_code == 429
    assert _count(db, location) == 5


# ──────────────────────────────────────────
# RS-13 (d): 3 активные → 409
# ──────────────────────────────────────────
def test_rs13_d_fourth_active_returns_409(db, client, restaurant, location):
    _as_guest(restaurant)
    # Старше 60 минут: лимит 60 минут не мешает, проверяется только лимит активных.
    for st in ("new", "confirmed", "new"):
        _add(db, location, status=st, age=timedelta(hours=2))

    r = _post(client, location)

    assert r.status_code == 409
    assert "лимит активных" in r.json()["detail"]
    assert _count(db, location) == 3


def test_rs13_d_third_active_passes(db, client, restaurant, location):
    _as_guest(restaurant)
    for st in ("new", "confirmed"):
        _add(db, location, status=st, age=timedelta(hours=2))

    assert _post(client, location).status_code == 201


# ──────────────────────────────────────────
# RS-13 (e): неактивные статусы не считаются активными
# ──────────────────────────────────────────
def test_rs13_e_completed_and_cancelled_are_not_active(db, client, restaurant, location):
    _as_guest(restaurant)
    for st in ("completed", "cancelled", "completed"):
        _add(db, location, status=st, age=timedelta(hours=2))

    assert _post(client, location).status_code == 201


# ──────────────────────────────────────────
# RS-13 (f): разные форматы одного номера — одна пара
# ──────────────────────────────────────────
def test_rs13_f_phone_formats_are_one_pair(db, client, restaurant, location):
    _as_guest(restaurant)
    formats = ["+998 (90) 123-45-67", "998901234567", "+998 90 123 45 67"]
    rows = [_add(db, location, phone=p, age=timedelta(hours=2)) for p in formats]

    assert {r.client_phone_digits for r in rows} == {"998901234567"}
    assert _post(client, location, phone="+998-90-123-45-67").status_code == 409


# ──────────────────────────────────────────
# RS-13 (g): другая Location / другой телефон — независимые счётчики
# ──────────────────────────────────────────
def test_rs13_g_other_location_and_other_phone_are_independent(
    db, client, restaurant, location, location_a2
):
    _as_guest(restaurant)
    for _ in range(3):
        _add(db, location, status="new", age=timedelta(hours=2))

    other_location = _post(client, location_a2)                 # та же пара телефона, другая Location
    other_phone = _post(client, location, phone=OTHER_PHONE)   # та же Location, другой телефон
    same_pair = _post(client, location)

    assert other_location.status_code == 201
    assert other_phone.status_code == 201
    assert same_pair.status_code == 409


# ──────────────────────────────────────────
# RS-13 (h): превышены оба лимита → 429
# ──────────────────────────────────────────
def test_rs13_h_both_limits_exceeded_returns_429(db, client, restaurant, location):
    _as_guest(restaurant)
    for st in ("new", "confirmed", "new", "completed", "cancelled"):
        _add(db, location, status=st, age=timedelta(minutes=5))   # 5 за час, 3 активные

    assert _post(client, location).status_code == 429


# ──────────────────────────────────────────
# RS-13 (i): брони Verified входят в счёт для Guest
# ──────────────────────────────────────────
def test_rs13_i_verified_reservations_count_for_next_guest_request(db, client, restaurant, location):
    # По умолчанию клиент авторизован как verified tg_user.
    for _ in range(3):
        assert _post(client, location).status_code == 201

    _as_guest(restaurant)
    assert _post(client, location).status_code == 409


# ──────────────────────────────────────────
# RS-13 (j): Verified лимитам MC-07 не подчиняется
# ──────────────────────────────────────────
def test_rs13_j_verified_user_is_not_subject_to_guest_limits(db, client, location):
    for st in ("new", "confirmed", "new", "completed", "cancelled"):
        _add(db, location, status=st, age=timedelta(minutes=5))   # у пары уже превышены оба лимита

    assert _post(client, location).status_code == 201


# ──────────────────────────────────────────
# RS-13 (k): IP rate limit 10/мин сохраняется
# ──────────────────────────────────────────
def test_rs13_k_ip_rate_limit_still_applies_to_guest(db, client, restaurant, location):
    _as_guest(restaurant)
    responses = [_post(client, location, phone=f"+99890000{i:04d}") for i in range(11)]

    assert [r.status_code for r in responses[:10]] == [201] * 10
    assert responses[10].status_code == 429
    assert "броней" not in responses[10].text      # это IP-лимит slowapi, не MC-07


def test_rs13_k_ip_rate_limit_still_applies_to_verified(db, client, location):
    codes = [_post(client, location, phone=f"+99891000{i:04d}").status_code for i in range(11)]

    assert codes[:10] == [201] * 10
    assert codes[10] == 429


# ──────────────────────────────────────────
# RS-13 (l): значения 5 и 3 берутся из config.py
# ──────────────────────────────────────────
def test_rs13_l_limit_values_come_from_config(db, client, restaurant, location, monkeypatch):
    assert settings.RESERVATION_GUEST_MAX_CREATED_PER_60_MIN == 5
    assert settings.RESERVATION_GUEST_MAX_ACTIVE == 3
    _as_guest(restaurant)

    monkeypatch.setattr(settings, "RESERVATION_GUEST_MAX_ACTIVE", 1)
    _add(db, location, status="new", age=timedelta(hours=2))
    assert _post(client, location).status_code == 409

    monkeypatch.setattr(settings, "RESERVATION_GUEST_MAX_CREATED_PER_60_MIN", 2)
    for _ in range(2):
        _add(db, location, phone=OTHER_PHONE, status="completed", age=timedelta(minutes=5))
    assert _post(client, location, phone=OTHER_PHONE).status_code == 429


# ──────────────────────────────────────────
# RS-13 (o): Redis и таблицы счётчиков нет
# ──────────────────────────────────────────
# ──────────────────────────────────────────
# Окно 60 минут: границы (детерминированно, через now)
# ──────────────────────────────────────────
def _fill_window(db, location, age, now, n=5):
    for _ in range(n):
        _add(db, location, status="completed", age=age, now=now)


def test_window_exactly_60_minutes_is_counted(db, restaurant, location):
    now = datetime.now(UTC)
    _fill_window(db, location, timedelta(minutes=60), now)       # created_at == now - 60 мин
    with pytest.raises(HTTPException) as exc:
        _service_create(db, restaurant, location, now=now)
    assert exc.value.status_code == 429


def test_window_less_than_60_minutes_is_counted(db, restaurant, location):
    now = datetime.now(UTC)
    _fill_window(db, location, timedelta(minutes=59, seconds=59), now)
    with pytest.raises(HTTPException) as exc:
        _service_create(db, restaurant, location, now=now)
    assert exc.value.status_code == 429


def test_window_older_than_60_minutes_is_not_counted(db, restaurant, location):
    now = datetime.now(UTC)
    _fill_window(db, location, timedelta(minutes=60, seconds=1), now)
    created = _service_create(db, restaurant, location, now=now)
    assert created.id is not None


def test_window_counts_only_recent_among_several(db, restaurant, location):
    now = datetime.now(UTC)
    for _ in range(3):
        _add(db, location, status="completed", age=timedelta(minutes=61), now=now)   # вне окна
    for _ in range(2):
        _add(db, location, status="completed", age=timedelta(minutes=30), now=now)   # в окне
    assert _service_create(db, restaurant, location, now=now).id is not None         # 3-я в окне


def test_window_four_recent_allow_one_more_then_limit(db, restaurant, location):
    now = datetime.now(UTC)
    for _ in range(4):
        _add(db, location, status="completed", age=timedelta(minutes=10), now=now)
    _add(db, location, status="completed", age=timedelta(minutes=90), now=now)       # вне окна
    assert _service_create(db, restaurant, location, now=now).id is not None          # 5-я в окне
    # Новая бронь имеет created_at (серверное время ≥ now) и входит в окно (нет верхней границы).
    with pytest.raises(HTTPException) as exc:
        _service_create(db, restaurant, location, now=now)
    assert exc.value.status_code == 429


# ──────────────────────────────────────────
# Телефон без цифр (допускается существующей валидацией _PHONE_RE) — одна пара ''
# ──────────────────────────────────────────
def test_digitless_phone_is_limited_as_one_pair(db, client, restaurant, location):
    """
    Существующая валидация телефона допускает строку без цифр (например '((((((( ').
    Её digits-значение пустое; все такие номера образуют одну пару (Location, ''),
    поэтому лимиты MC-07 действуют и обойти их «телефоном без цифр» нельзя.
    Новых правил валидации Phase 15 не добавляет.
    """
    _as_guest(restaurant)
    for variant in ("(((((((", "-------", "( ) ( ) ("):
        assert _post(client, location, phone=variant).status_code == 201
    assert _post(client, location, phone="))))))))").status_code == 409


# ──────────────────────────────────────────
# Телефон: нормализация и client_phone_digits
# ──────────────────────────────────────────
def test_client_phone_digits_is_filled_for_every_new_reservation(db, client, restaurant, location):
    # Verified через API
    assert _post(client, location, phone="+998 (90) 111-22-33").status_code == 201
    # Guest через API
    _as_guest(restaurant)
    assert _post(client, location, phone="+998 (91) 111-22-33").status_code == 201
    # Прямой INSERT (фикстуры, будущие пути создания): значение ставит модель
    direct = _add(db, location, phone="+998 (92) 111-22-33")

    rows = db.query(Reservation).filter(Reservation.location_id == location.id).all()
    digits = {r.client_phone: r.client_phone_digits for r in rows}
    assert digits["+998 (90) 111-22-33"] == "998901112233"
    assert digits["+998 (91) 111-22-33"] == "998911112233"
    assert direct.client_phone_digits == "998921112233"
    assert all(v for v in digits.values())


# ──────────────────────────────────────────
# Регрессия: существующие контракты
# ──────────────────────────────────────────
def test_regression_guest_normal_create_keeps_contract(db, client, restaurant, location):
    _as_guest(restaurant)
    r = _post(client, location)

    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "new"
    assert body["location_id"] == location.id
    assert set(body) == {
        "id", "status", "client_name", "client_phone", "guests_count",
        "reservation_time", "comment", "created_at", "location_id",
    }


def test_regression_location_isolation_and_inactive_location(db, client, restaurant, location, location2):
    _as_guest(restaurant)
    assert _post(client, location2).status_code == 404      # Location чужого ресторана
    location.is_active = False
    db.flush()
    assert _post(client, location).status_code == 404        # неактивная Location


def test_regression_verified_create_keeps_contract(db, client, location):
    r = _post(client, location)
    assert r.status_code == 201 and r.json()["status"] == "new"


# ══════════════════════════════════════════
# Параллельные запросы и advisory lock (RS-13 n, G-15)
# ══════════════════════════════════════════
class _PgWorld:
    """Закоммиченные данные (нужны параллельным соединениям) + гарантированная очистка."""

    def __init__(self):
        self.engine = create_engine(os.environ["DATABASE_URL"])
        self.Session = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        suffix = uuid.uuid4().hex[:10]
        s = self.Session()
        try:
            agency = Agency(
                name=f"mc07-{suffix}",
                owner_email=f"mc07-{suffix}@test.uz",
                owner_password_hash=hash_password("password123"),
            )
            s.add(agency)
            s.flush()
            restaurant = Restaurant(
                agency_id=agency.id, name=f"mc07-{suffix}", slug=f"mc07-{suffix}",
                admin_password_hash=hash_password("secret"),
                primary_color="#8B1A2E", secondary_color="#FAF6EE", accent_color="#D4A853",
                currency="UZS",
            )
            s.add(restaurant)
            s.flush()
            location = Location(
                restaurant_id=restaurant.id, name="mc07", slug=f"mc07-{suffix}",
                is_active=True, timezone="Asia/Tashkent", delivery_fee=0,
                min_order_amount=0, currency="UZS", language="uz",
                is_waiter_call_enabled=False,
            )
            s.add(location)
            s.flush()
            self.agency_id, self.restaurant_id, self.location_id = agency.id, restaurant.id, location.id
            s.commit()
        finally:
            s.close()

    def prefill(self, n, status, age, phone=PHONE):
        s = self.Session()
        try:
            for _ in range(n):
                s.add(Reservation(
                    restaurant_id=self.restaurant_id, location_id=self.location_id,
                    client_name="prefill", client_phone=phone, guests_count=2,
                    reservation_time=_future() + timedelta(days=1), status=status,
                    created_at=datetime.now(UTC) - age,
                ))
            s.commit()
        finally:
            s.close()

    def count(self, phone=PHONE) -> int:
        s = self.Session()
        try:
            return s.query(func.count(Reservation.id)).filter(
                Reservation.location_id == self.location_id,
                Reservation.client_phone_digits == phone_digits(phone),
            ).scalar()
        finally:
            s.close()

    def attempt(self, phone, results, barrier=None):
        s = self.Session()
        try:
            restaurant = s.get(Restaurant, self.restaurant_id)
            data = ReservationCreate(
                client_name="Параллель", client_phone=phone, guests_count=2,
                reservation_time=_future(),
            )
            if barrier is not None:
                barrier.wait(timeout=15)
            try:
                res_service.create_reservation(
                    s, tg_user=_guest(restaurant), location_id=self.location_id, data=data
                )
                results.append(201)
            except HTTPException as exc:
                results.append(exc.status_code)
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


def _run_parallel(world, phones):
    results: list[int] = []
    barrier = threading.Barrier(len(phones))
    threads = [
        threading.Thread(target=world.attempt, args=(p, results, barrier)) for p in phones
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "поток завис (возможна взаимная блокировка)"
    return results


@pytest.mark.postgres
def test_rs13_n_parallel_guest_requests_cannot_exceed_active_limit(pg_world):
    results = _run_parallel(pg_world, [PHONE] * 8)

    assert results.count(201) == 3
    assert results.count(409) == 5
    assert pg_world.count() == 3


@pytest.mark.postgres
def test_rs13_n_parallel_guest_requests_cannot_exceed_created_limit(pg_world):
    pg_world.prefill(3, "completed", timedelta(minutes=10))   # 3 из 5 «созданных» уже есть

    results = _run_parallel(pg_world, [PHONE] * 8)

    assert results.count(201) == 2
    assert results.count(429) == 6
    assert pg_world.count() == 5


@pytest.mark.postgres
def test_rs13_n_different_pairs_are_limited_independently(pg_world):
    results = _run_parallel(pg_world, [PHONE] * 4 + [OTHER_PHONE] * 4)

    assert results.count(201) == 6          # по 3 на каждую пару
    assert results.count(409) == 2
    assert pg_world.count(PHONE) == 3 and pg_world.count(OTHER_PHONE) == 3


@pytest.mark.postgres
def test_rs13_n_advisory_lock_blocks_same_pair_only(pg_world):
    digits_a = phone_digits(PHONE)
    key_a = res_service.guest_limit_lock_key(pg_world.location_id, digits_a)

    holder = pg_world.engine.connect()
    tx = holder.begin()
    try:
        holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key_a})

        # Другая пара не блокируется.
        other: list[int] = []
        t_other = threading.Thread(target=pg_world.attempt, args=(OTHER_PHONE, other))
        started = time.monotonic()
        t_other.start()
        t_other.join(timeout=10)
        assert not t_other.is_alive(), "другая пара не должна ждать чужой lock"
        assert other == [201]
        assert time.monotonic() - started < 10

        # Та же пара ждёт, пока lock удерживается.
        same: list[int] = []
        t_same = threading.Thread(target=pg_world.attempt, args=(PHONE, same))
        t_same.start()
        t_same.join(timeout=1.5)
        assert t_same.is_alive(), "запрос той же пары должен ждать advisory lock"
        assert same == []
    finally:
        tx.commit()          # снимает transaction-level lock
        holder.close()

    t_same.join(timeout=15)
    assert not t_same.is_alive()
    assert same == [201]
