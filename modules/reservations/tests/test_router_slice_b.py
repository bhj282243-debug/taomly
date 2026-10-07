"""
modules/reservations/tests/test_router_slice_b.py — Phase 15, Slice B (R1), File #10

Тесты Reservation API (router): Idempotency-Key, коды 201/200/409/403/404/422,
заголовок Idempotent-Replayed, переходы статусов, actor_ref, флаг Location.
Не заменяют и не меняют набор MC-07 (test_guest_limits.py).
"""

from datetime import UTC, datetime, timedelta
import logging

import pytest

from api import app
from auth import TelegramUser, get_current_restaurant_admin, get_telegram_user
from models import Reservation
from modules.access import Principal
from modules.reservations import service as res_service

pytestmark = pytest.mark.postgres   # MC-09: как MC-07 — на SQLite-job пропускаются

URL = "/api/reservations/"
KEY = "route-key-0001"
OTHER_KEY = "route-key-0002"

# Фиксированное время брони: точный повтор обязан совпасть по reservation_time.
RT = datetime.now(UTC) + timedelta(days=1)


def _payload(**over) -> dict:
    body = {
        "client_name": "Тест Роутер",
        "client_phone": "+998901234567",
        "guests_count": 2,
        "reservation_time": RT.isoformat(),
        "comment": None,
    }
    body.update(over)
    return body


def _post(client, key=None, **over):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(URL, json=_payload(**over), headers=headers)


def _patch(client, reservation_id, target):
    return client.patch(f"{URL}{reservation_id}/status", json={"status": target})


def _guest(restaurant) -> TelegramUser:
    return TelegramUser(
        id=0, first_name="Guest", last_name=None, username=None, language_code="uz",
        restaurant_id=restaurant.id, restaurant=restaurant,
    )


def _count(db, location) -> int:
    return db.query(Reservation).filter(Reservation.location_id == location.id).count()


# ──────────────────────────────────────────
# POST: создание и Idempotency-Key
# ──────────────────────────────────────────
def test_create_without_key_returns_201_without_replay_header(client):
    r = _post(client)
    assert r.status_code == 201
    assert r.json()["status"] == "requested"
    assert "idempotent-replayed" not in r.headers


def test_create_with_valid_key_returns_201(client, db, location):
    r = _post(client, KEY)
    assert r.status_code == 201
    assert "idempotent-replayed" not in r.headers
    assert _count(db, location) == 1


def test_exact_replay_returns_200_with_replayed_header(client, db, location):
    first = _post(client, KEY)
    again = _post(client, KEY)
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert _count(db, location) == 1


def test_guest_exact_replay_returns_200(client, db, location, restaurant):
    app.dependency_overrides[get_telegram_user] = lambda: _guest(restaurant)
    first = _post(client, KEY)
    again = _post(client, KEY)
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert _count(db, location) == 1


def test_replay_with_different_data_returns_409_and_creates_nothing(client, db, location):
    _post(client, KEY)
    r = _post(client, KEY, client_name="Другой клиент")
    assert r.status_code == 409
    assert "Тест Роутер" not in r.text and "+998901234567" not in r.text
    assert "idempotent-replayed" not in r.headers
    assert _count(db, location) == 1


@pytest.mark.parametrize(
    "bad_key",
    ["short", "has space 12345", "x" * 65, "bad!char-1234", "dots.not.allowed"],
)
def test_invalid_idempotency_key_returns_422(client, db, location, bad_key):
    r = _post(client, bad_key)
    assert r.status_code == 422
    assert _count(db, location) == 0


def test_disabled_location_blocks_new_reservation_with_403(client, db, location):
    location.is_reservation_enabled = False
    db.flush()
    r = _post(client, KEY)
    assert r.status_code == 403
    assert _count(db, location) == 0


def test_existing_reservation_stays_manageable_and_replayable_when_disabled(
    client, db, location
):
    reservation_id = _post(client, KEY).json()["id"]
    location.is_reservation_enabled = False
    db.flush()

    assert _post(client, OTHER_KEY).status_code == 403          # новая — нельзя
    assert _patch(client, reservation_id, "confirmed").status_code == 200   # существующая — можно
    again = _post(client, KEY)                                   # replay — не новая
    assert again.status_code == 200 and again.json()["id"] == reservation_id


# ──────────────────────────────────────────
# PATCH: переходы статусов
# ──────────────────────────────────────────
def test_valid_status_transition_returns_200_and_sets_timestamp(client):
    reservation_id = _post(client).json()["id"]
    r = _patch(client, reservation_id, "confirmed")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_at"] is not None
    assert body["seated_at"] is None


def test_invalid_status_transition_returns_409(client):
    reservation_id = _post(client).json()["id"]
    skip = _patch(client, reservation_id, "seated")           # requested -> seated запрещён
    assert skip.status_code == 409
    assert _patch(client, reservation_id, "confirmed").status_code == 200
    repeat = _patch(client, reservation_id, "confirmed")      # повтор
    assert repeat.status_code == 409


@pytest.mark.parametrize("target", ["new", "requested", "unknown"])
def test_forbidden_target_statuses_return_422(client, target):
    reservation_id = _post(client).json()["id"]
    assert _patch(client, reservation_id, target).status_code == 422


def test_terminal_status_has_no_further_transitions(client):
    reservation_id = _post(client).json()["id"]
    assert _patch(client, reservation_id, "cancelled").status_code == 200
    assert _patch(client, reservation_id, "confirmed").status_code == 409


# ──────────────────────────────────────────
# actor_ref и доступ
# ──────────────────────────────────────────
def test_actor_ref_for_guest_and_verified_telegram(restaurant):
    assert res_service._client_principal(_guest(restaurant)).actor_ref == "guest"
    verified = TelegramUser(
        id=123456789, first_name="V", last_name=None, username=None, language_code="uz",
        restaurant_id=restaurant.id, restaurant=restaurant,
    )
    assert res_service._client_principal(verified).actor_ref == "telegram:123456789"


def test_actor_ref_for_restaurant_admin_is_taken_from_token(client, restaurant, caplog):
    reservation_id = _post(client).json()["id"]
    with caplog.at_level(logging.INFO, logger="modules.reservations.service"):
        assert _patch(client, reservation_id, "confirmed").status_code == 200
    assert f"actor=restaurant_admin:{restaurant.id}" in caplog.text


def test_out_of_scope_location_returns_403(client, restaurant, location, monkeypatch):
    reservation_id = _post(client).json()["id"]
    scoped = Principal(
        restaurant, frozenset({location.id + 1000}), f"restaurant_admin:{restaurant.id}"
    )
    monkeypatch.setattr("routers.reservations.restaurant_admin_principal", lambda _r: scoped)
    assert _patch(client, reservation_id, "confirmed").status_code == 403


def test_foreign_restaurant_admin_gets_404(client, restaurant2):
    reservation_id = _post(client).json()["id"]
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant2
    assert _patch(client, reservation_id, "confirmed").status_code == 404
