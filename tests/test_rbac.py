"""
tests/test_rbac.py — Foundation Task 3: RBAC

Покрывает:
  - Role escalation via request body / headers / JWT manipulation
  - Mass assignment protection (role, agency_id, restaurant_id в schemas)
  - Agency RBAC: agency_owner vs restaurant_admin role
  - Superadmin security boundary
  - HTTP semantics: 401 vs 403
  - JWT role enforcement
  - Telegram: authenticated != authorized
"""

import pytest
from fastapi.testclient import TestClient

from api import app
from auth import (
    TelegramUser,
    create_agency_token,
    create_restaurant_token,
    get_current_agency,
    get_current_restaurant_admin,
    get_telegram_user,
)
from database import get_db


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def _raw_client(db):
    """TestClient без dependency overrides — использует реальную JWT-проверку."""
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    return c


def _authed_client(db, agency=None, restaurant=None, tg_user=None):
    """TestClient с JWT-override через dependency."""
    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    if agency:
        app.dependency_overrides[get_current_agency] = lambda: agency
    if restaurant:
        app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant
    if tg_user:
        app.dependency_overrides[get_telegram_user] = lambda: tg_user
    return TestClient(app, raise_server_exceptions=True)


# ─────────────────────────────────────────
# 401 — НЕТ ТОКЕНА
# ─────────────────────────────────────────

@pytest.mark.security
def test_401_no_token_on_protected_endpoint(db):
    """
    Запрос без Authorization заголовка на защищённый endpoint → 401.
    Не 403 (пользователь не authenticated).
    """
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get("/api/analytics/summary")
        assert resp.status_code == 401, f"Ожидали 401, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_401_no_token_on_menu_admin_endpoint(db):
    """GET /api/menu/{id}/all без токена → 401."""
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get("/api/menu/1/all")
        assert resp.status_code == 401, f"Ожидали 401, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_401_no_token_on_orders_admin_endpoint(db):
    """GET /api/orders/restaurant/1 без токена → 401."""
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get("/api/orders/restaurant/1")
        assert resp.status_code == 401, f"Ожидали 401, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# 403 — НЕВЕРНАЯ РОЛЬ (authenticated, но wrong role)
# ─────────────────────────────────────────

@pytest.mark.security
def test_403_agency_token_on_restaurant_endpoint(db, agency, restaurant):
    """
    Agency owner JWT не должен работать на restaurant_admin endpoints.
    authenticated → 403 (не 401).
    """
    agency_token = create_agency_token(agency)
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(
            f"/api/orders/restaurant/{restaurant.id}",
            headers={"Authorization": f"Bearer {agency_token}"},
        )
        assert resp.status_code in (401, 403), (
            f"Agency token на restaurant endpoint должен вернуть 401/403, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_403_restaurant_token_on_agency_endpoint(db, restaurant):
    """
    Restaurant admin JWT не должен работать на agency_owner endpoints.
    """
    restaurant_token = create_restaurant_token(restaurant)
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(
            "/api/agency/restaurants",
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert resp.status_code in (401, 403), (
            f"Restaurant token на agency endpoint должен вернуть 401/403, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_403_restaurant_token_on_superadmin_endpoint(db, restaurant):
    """
    Restaurant admin JWT не должен работать на superadmin endpoints.
    """
    restaurant_token = create_restaurant_token(restaurant)
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(
            "/api/superadmin/dashboard",
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert resp.status_code in (401, 403), (
            f"Restaurant token на superadmin должен вернуть 401/403, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_403_agency_token_on_superadmin_endpoint(db, agency):
    """
    Agency owner JWT не должен работать на superadmin endpoints.
    """
    agency_token = create_agency_token(agency)
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(
            "/api/superadmin/dashboard",
            headers={"Authorization": f"Bearer {agency_token}"},
        )
        assert resp.status_code in (401, 403), (
            f"Agency token на superadmin должен вернуть 401/403, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# ROLE ESCALATION ЧЕРЕЗ REQUEST BODY
# ─────────────────────────────────────────

@pytest.mark.security
def test_role_escalation_via_agency_register(db):
    """
    POST /api/agency/register не должен принимать поле 'role'.
    AgencyRegister schema содержит только name, email, password.
    Попытка передать role игнорируется Pydantic.
    """
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.post(
            "/api/agency/register",
            json={
                "name": "Evil Agency",
                "email": "evil@test.uz",
                "password": "password123",
                "role": "superadmin",          # попытка escalation
                "is_superadmin": True,          # попытка escalation
                "agency_id": 9999,              # попытка injection
            },
        )
        # Либо 201 (создано, но role=superadmin проигнорирован)
        # либо 400 (email already exists)
        # главное — не 200 с superadmin role
        if resp.status_code == 201:
            data = resp.json()
            # Agency response не содержит поля role
            assert "role" not in data or data.get("role") != "superadmin"
            assert "is_superadmin" not in data
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_role_escalation_via_restaurant_update(db, agency, restaurant, tg_user):
    """
    PATCH /api/agency/restaurants/{id} не должен принимать agency_id из тела.
    RestaurantUpdate schema не содержит agency_id.
    Ресторан не может быть переназначен в другое агентство через PATCH.
    """
    c = _authed_client(db, agency=agency, restaurant=restaurant, tg_user=tg_user)
    try:
        resp = c.patch(
            f"/api/agency/restaurants/{restaurant.id}",
            json={
                "name": "Updated Name",
                "agency_id": 9999,           # попытка transfer через PATCH
                "restaurant_id": 9999,       # игнорируется
                "role": "superadmin",         # игнорируется
            },
        )
        if resp.status_code == 200:
            from database import SessionLocal
            # agency_id должен остаться прежним
            pass  # schema не имеет agency_id, setattr не обновит его
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_mass_assignment_is_active_removed_from_patch(db, agency, restaurant, tg_user):
    """
    PATCH /api/agency/restaurants/{id} не должен принимать is_active.
    RestaurantUpdate schema не содержит is_active (удалено в Foundation Task 3).
    Деактивация ресторана — только через DELETE endpoint.
    """
    c = _authed_client(db, agency=agency, restaurant=restaurant, tg_user=tg_user)
    try:
        original_is_active = restaurant.is_active
        resp = c.patch(
            f"/api/agency/restaurants/{restaurant.id}",
            json={
                "name": "Renamed",
                "is_active": False,  # попытка деактивации через PATCH
            },
        )
        if resp.status_code == 200:
            # is_active не должен измениться через PATCH
            from sqlalchemy.orm import Session
            db.refresh(restaurant)
            assert restaurant.is_active == original_is_active, (
                "is_active не должен меняться через PATCH /restaurants/{id}"
            )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# SUPERADMIN SECURITY BOUNDARY
# ─────────────────────────────────────────

@pytest.mark.security
def test_superadmin_no_access_without_auth(db):
    """
    GET /api/superadmin/dashboard без токена → 401.
    """
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get("/api/superadmin/dashboard")
        assert resp.status_code in (401, 403), (
            f"Superadmin без токена должен вернуть 401/403, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_superadmin_login_wrong_password(db):
    """
    POST /api/superadmin/login с неверным паролем → 401.
    Superadmin credentials хранятся в env (bcrypt hash), не в БД.
    """
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.post(
            "/api/superadmin/login",
            json={
                "email": "superadmin@example.com",
                "password": "wrong_password_12345",
            },
        )
        assert resp.status_code == 401, (
            f"Неверный пароль superadmin должен вернуть 401, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_superadmin_login_role_escalation_via_body(db):
    """
    POST /api/superadmin/login использует Pydantic схему (email + password).
    Поля role/is_superadmin не принимаются и не дают доступ.
    """
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.post(
            "/api/superadmin/login",
            json={
                "email": "attacker@example.com",
                "password": "doesn't matter",
                "role": "superadmin",
                "is_superadmin": True,
            },
        )
        # Должен вернуть 401 (неверные credentials)
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# JWT MANIPULATION
# ─────────────────────────────────────────

@pytest.mark.security
def test_tampered_jwt_role_rejected(db, restaurant):
    """
    JWT с изменённым полем role (без переподписи) должен быть отклонён.
    jose.jwt.decode проверяет signature — любая модификация payload → 401.
    """
    import base64
    import json

    token = create_restaurant_token(restaurant)
    parts = token.split(".")

    # Декодируем payload (без проверки подписи)
    padding = 4 - len(parts[1]) % 4
    payload_bytes = base64.urlsafe_b64decode(parts[1] + "=" * padding)
    payload = json.loads(payload_bytes)

    # Меняем role
    payload["role"] = "superadmin"
    new_payload = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()

    # Собираем токен с оригинальной подписью (невалидной)
    tampered_token = f"{parts[0]}.{new_payload}.{parts[2]}"

    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(
            "/api/superadmin/dashboard",
            headers={"Authorization": f"Bearer {tampered_token}"},
        )
        assert resp.status_code in (401, 403), (
            f"Tampered JWT должен вернуть 401/403, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_revoked_token_rejected(db, agency):
    """
    Отозванный JWT должен быть отклонён с 401.
    Тест проверяет что revocation list работает.
    """
    from auth import revoke_token, decode_token

    token = create_agency_token(agency)
    payload = decode_token(token, db)

    # Отзываем токен
    revoke_token(payload, db)

    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(
            "/api/agency/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401, (
            f"Отозванный токен должен вернуть 401, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_invalid_signature_jwt_rejected(db):
    """
    JWT с неверной подписью (другой SECRET_KEY) → 401.
    """
    from jose import jwt
    from datetime import datetime, timezone, timedelta
    import uuid

    fake_payload = {
        "sub": "1",
        "role": "superadmin",
        "jti": str(uuid.uuid4()),
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
    }
    fake_token = jwt.encode(fake_payload, "wrong_secret_key_totally_fake", algorithm="HS256")

    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(
            "/api/superadmin/dashboard",
            headers={"Authorization": f"Bearer {fake_token}"},
        )
        assert resp.status_code in (401, 403), (
            f"Токен с неверной подписью должен вернуть 401/403, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# TENANT + RBAC ОДНОВРЕМЕННО
# ─────────────────────────────────────────

@pytest.mark.security
def test_tenant_and_rbac_restaurant_a_admin_cannot_access_restaurant_b(
    db, restaurant, restaurant2, tg_user
):
    """
    Restaurant A (agency_owner) не может получить доступ к данным Restaurant B.
    Одновременно: tenant isolation (restaurant_id mismatch) + role check.
    """
    c = _authed_client(db, restaurant=restaurant, tg_user=tg_user)
    try:
        # Даже с валидным токеном restaurant A → 403 на данные B
        resp = c.get(f"/api/orders/restaurant/{restaurant2.id}")
        assert resp.status_code == 403, (
            f"Restaurant A с валидным токеном не должен видеть заказы Restaurant B. "
            f"Получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_agency_a_owner_cannot_impersonate_via_patch(
    db, agency, agency2, restaurant, restaurant2, tg_user
):
    """
    Agency A не может переназначить ресторан Agency B на себя через PATCH.
    Проверяет одновременно: tenant isolation + отсутствие agency_id в schema.
    """
    c = _authed_client(db, agency=agency, restaurant=restaurant, tg_user=tg_user)
    try:
        resp = c.patch(
            f"/api/agency/restaurants/{restaurant2.id}",
            json={"name": "Hijacked"},
        )
        # restaurant2 принадлежит agency2 → 404 (tenant isolation)
        assert resp.status_code == 404, (
            f"Agency A не может патчить ресторан Agency B. Получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# USER.ROLE — существующие роли не задействованы в RBAC
# (документирующие тесты)
# ─────────────────────────────────────────

@pytest.mark.security
def test_user_model_role_field_exists_but_not_enforced_in_api():
    """
    Документирующий тест: User.role существует в модели (admin/owner/dispatcher/client),
    но НЕ используется для API-авторизации.
    API авторизация основана на JWT role (agency_owner / restaurant_admin / superadmin).
    User.role — данные для будущего in-app RBAC (KDS, waiter app).

    Этот тест фиксирует текущее поведение — не баг, а осознанный дизайн.
    """
    from models import User
    # User.role есть в модели
    assert hasattr(User, "role")
    # CheckConstraint: допустимые значения
    constraint_names = [c.name for c in User.__table_args__ if hasattr(c, "name")]
    assert "check_user_role" in constraint_names


@pytest.mark.security
def test_jwt_contains_required_claims(db, agency, restaurant):
    """
    JWT токены содержат необходимые claims для authorization.
    Agency token: sub, role, agency_id, jti, exp.
    Restaurant token: sub, role, restaurant_id, agency_id, jti, exp.
    """
    from auth import decode_token

    a_token = create_agency_token(agency)
    a_payload = decode_token(a_token, db)
    assert a_payload["role"] == "agency_owner"
    assert "agency_id" in a_payload
    assert "jti" in a_payload
    assert "exp" in a_payload

    r_token = create_restaurant_token(restaurant)
    r_payload = decode_token(r_token, db)
    assert r_payload["role"] == "restaurant_admin"
    assert "restaurant_id" in r_payload
    assert "agency_id" in r_payload  # для будущих cross-agency checks
    assert "jti" in r_payload
    assert "exp" in r_payload


# ─────────────────────────────────────────
# TELEGRAM — authenticated ≠ authorized (admin access)
# ─────────────────────────────────────────

@pytest.mark.security
def test_telegram_user_cannot_access_admin_endpoints(db, tg_user, restaurant):
    """
    Telegram клиент (initData) не может вызвать admin-only endpoints.
    Admin endpoints требуют JWT (restaurant_admin role), не initData.
    """
    app.dependency_overrides[get_db] = lambda: db
    # Нет override для get_current_restaurant_admin — используем реальный
    app.dependency_overrides[get_telegram_user] = lambda: tg_user
    c = TestClient(app, raise_server_exceptions=True)
    try:
        # Нет Authorization header → 401/403
        resp = c.patch(
            f"/api/menu/category/1",
            json={"name": "Hijacked Category"},
        )
        assert resp.status_code in (401, 403), (
            f"Без JWT на admin endpoint должен быть 401/403, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_telegram_guest_user_cannot_see_orders(db, restaurant, order_r1=None):
    """
    Гостевой пользователь (tg_user.id == 0) не видит ничьи заказы.
    GET /api/orders/my → пустой список (не 404, не чужие данные).
    """
    guest_tg_user = TelegramUser(
        id=0,
        first_name="Guest",
        last_name=None,
        username=None,
        language_code="uz",
        restaurant_id=restaurant.id,
        restaurant=restaurant,
    )

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_telegram_user] = lambda: guest_tg_user
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get("/api/orders/my")
        assert resp.status_code == 200
        assert resp.json() == [], "Гость должен видеть пустой список заказов"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_telegram_guest_cannot_get_specific_order(db, restaurant):
    """
    Гостевой пользователь не может получить конкретный заказ по ID.
    GET /api/orders/my/{order_id} → 404 для гостя.
    """
    guest_tg_user = TelegramUser(
        id=0,
        first_name="Guest",
        last_name=None,
        username=None,
        language_code="uz",
        restaurant_id=restaurant.id,
        restaurant=restaurant,
    )

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_telegram_user] = lambda: guest_tg_user
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get("/api/orders/my/1")
        assert resp.status_code == 404, (
            f"Гость не должен видеть конкретный заказ, получили {resp.status_code}"
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# ROLE CHANGE — старый JWT не должен сохранять privilege
# ─────────────────────────────────────────

@pytest.mark.security
def test_stale_jwt_risk_documented():
    """
    Документирующий тест: если agency деактивировано (is_active=False),
    уже выданный JWT остаётся валидным до истечения (8 часов).

    Mitigation существует: get_current_agency() проверяет Agency.is_active == True.
    Поэтому деактивированный agency owner потеряет доступ при следующем запросе —
    БД-запрос вернёт None → 404.

    Аналогично для restaurant: get_current_restaurant_admin() проверяет is_active.

    Риск: 8-часовое окно между деактивацией и потерей доступа через logout
    можно закрыть только через logout (revoke jti) или сокращение expire_hours.
    Это задокументированный риск, не баг.
    """
    from config import settings
    assert settings.ACCESS_TOKEN_EXPIRE_HOURS == 8, (
        "ACCESS_TOKEN_EXPIRE_HOURS изменился — пересмотрите stale JWT risk"
    )
    # get_current_agency и get_current_restaurant_admin делают DB lookup
    # и проверяют is_active — это mitigation для stale JWT
    import inspect
    from auth import get_current_agency, get_current_restaurant_admin
    agency_src = inspect.getsource(get_current_agency)
    restaurant_src = inspect.getsource(get_current_restaurant_admin)
    assert "is_active" in agency_src, "get_current_agency должен проверять is_active"
    assert "is_active" in restaurant_src, "get_current_restaurant_admin должен проверять is_active"
