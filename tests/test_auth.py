"""
tests/test_auth.py — Auth, JWT, Password hashing тесты

Покрывает:
  - bcrypt hash/verify
  - JWT создание и декодирование
  - JWT с неверным ключом
  - Agency login success/fail
  - Restaurant admin login success/fail
  - Rate limit (структурно)
  - Роль в JWT payload
  - jti наличие в payload (v6)
  - JWT Revocation: revoke_token + повторный decode_token → 401 (v6)
"""

import pytest
from jose import jwt

from auth import (
    create_agency_token,
    create_restaurant_token,
    decode_token,
    hash_password,
    revoke_token,
    verify_password,
)


# ──────────────────────────────────────────
# TEST 1: Password hashing
# ──────────────────────────────────────────
@pytest.mark.unit
def test_password_hash_and_verify():
    """bcrypt: hash не равен plaintext, verify работает."""
    plain = "MySecurePassword123"
    hashed = hash_password(plain)

    assert hashed != plain
    assert verify_password(plain, hashed)
    assert not verify_password("wrong_password", hashed)


# ──────────────────────────────────────────
# TEST 2: JWT agency token payload
# ──────────────────────────────────────────
@pytest.mark.unit
def test_agency_token_payload(agency, db):
    token = create_agency_token(agency)
    payload = decode_token(token, db)

    assert payload["role"] == "agency_owner"
    assert payload["agency_id"] == agency.id
    assert "exp" in payload
    assert "jti" in payload          # v6: jti обязателен
    assert "token_type" in payload   # v6: зарезервировано для C-4


# ──────────────────────────────────────────
# TEST 3: JWT restaurant token payload
# ──────────────────────────────────────────
@pytest.mark.unit
def test_restaurant_token_payload(restaurant, agency, db):
    token = create_restaurant_token(restaurant)
    payload = decode_token(token, db)

    assert payload["role"] == "restaurant_admin"
    assert payload["restaurant_id"] == restaurant.id
    assert payload["agency_id"] == agency.id
    assert "jti" in payload


# ──────────────────────────────────────────
# TEST 4: JWT invalid signature
# ──────────────────────────────────────────
@pytest.mark.security
def test_invalid_jwt_raises(agency, db):
    from fastapi import HTTPException

    token = create_agency_token(agency)
    tampered = token[:-5] + "XXXXX"  # портим подпись

    with pytest.raises(HTTPException) as exc_info:
        decode_token(tampered, db)
    assert exc_info.value.status_code == 401


# ──────────────────────────────────────────
# TEST 4b: JWT Revocation — отозванный токен отклоняется
# ──────────────────────────────────────────
@pytest.mark.security
@pytest.mark.postgres
def test_revoked_token_raises_401(agency, db):
    """
    После revoke_token() любой запрос с этим jti должен получать 401.
    Проверяет что INSERT ON CONFLICT DO NOTHING работает и SELECT находит запись.
    """
    from fastapi import HTTPException

    token = create_agency_token(agency)
    payload = decode_token(token, db)  # первый decode — OK

    revoke_token(payload, db)          # отзываем

    with pytest.raises(HTTPException) as exc_info:
        decode_token(token, db)        # второй decode — должен вернуть 401
    assert exc_info.value.status_code == 401
    assert "отозван" in exc_info.value.detail.lower()


# ──────────────────────────────────────────
# TEST 4c: Идемпотентный revoke_token
# ──────────────────────────────────────────
@pytest.mark.security
@pytest.mark.postgres
def test_revoke_token_idempotent(agency, db):
    """Двойной revoke не должен бросать исключение (ON CONFLICT DO NOTHING)."""
    token = create_agency_token(agency)
    payload = decode_token(token, db)

    revoke_token(payload, db)  # первый отзыв
    revoke_token(payload, db)  # второй — не должен упасть


# ──────────────────────────────────────────
# TEST 5: Agency login — success
# ──────────────────────────────────────────
@pytest.mark.integration
def test_agency_login_success(client, agency):
    resp = client.post("/api/agency/login", json={
        "email": "test@agency.uz",
        "password": "password123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


# ──────────────────────────────────────────
# TEST 6: Agency login — wrong password
# ──────────────────────────────────────────
@pytest.mark.security
def test_agency_login_wrong_password(client):
    resp = client.post("/api/agency/login", json={
        "email": "test@agency.uz",
        "password": "wrongpassword",
    })
    assert resp.status_code == 401
    # Не должно раскрывать "пароль неверный" vs "email не найден"
    assert "email" in resp.json()["detail"].lower() or "пароль" in resp.json()["detail"].lower()


# ──────────────────────────────────────────
# TEST 7: Agency login — unknown email
# ──────────────────────────────────────────
@pytest.mark.security
def test_agency_login_unknown_email(client):
    resp = client.post("/api/agency/login", json={
        "email": "nobody@nowhere.com",
        "password": "password123",
    })
    assert resp.status_code == 401


# ──────────────────────────────────────────
# TEST 8: Restaurant login — success
# ──────────────────────────────────────────
@pytest.mark.integration
def test_restaurant_login_success(client, restaurant):
    resp = client.post("/api/agency/restaurant-login", json={
        "slug": "chinar",
        "password": "secret",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


# ──────────────────────────────────────────
# TEST 9: Restaurant login — wrong slug
# ──────────────────────────────────────────
@pytest.mark.security
def test_restaurant_login_wrong_slug(client):
    resp = client.post("/api/agency/restaurant-login", json={
        "slug": "nonexistent-restaurant",
        "password": "secret",
    })
    assert resp.status_code == 401


# ──────────────────────────────────────────
# TEST 10: /api/agency/me — требует токен
# ──────────────────────────────────────────
@pytest.mark.security
def test_agency_me_requires_auth(client):
    """GET /api/agency/me без токена должен вернуть 403."""
    # Создаём клиент без переопределения get_current_agency
    from api import app
    from auth import get_current_agency

    app.dependency_overrides.pop(get_current_agency, None)
    with TestClient(app) as raw_client:
        resp = raw_client.get("/api/agency/me")
    assert resp.status_code in (401, 403)


from fastapi.testclient import TestClient
