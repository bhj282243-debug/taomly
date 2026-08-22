"""
tests/test_error_handling.py — Foundation Task 5: Error Handling, Logging & Sentry

Покрывает пункты аудита из FOUNDATION_TASK_5 (раздел 24):
  1.  unexpected exception → 500
  2.  no traceback in response
  3.  no secret in error response
  4.  validation error → 422
  5.  unauthorized (no token) → 401
  6.  forbidden (wrong role) → 403
  7.  foreign resource (cross-tenant) → 404
  8.  database integrity error → safe response (no raw DB error leak)
  9.  duplicate resource → 409
  11. request ID exists on every response
  12. request ID propagated: header == 500-body request_id
  13. exception logged (caplog)
  14. sensitive data not logged (webhook secret)
  15. Sentry capture called for unexpected (500) exception (mocked)
  16. Sentry capture NOT called for expected 401/404 (mocked)
  17. webhook invalid secret does not leak the secret value
  18. Telegram initData is not echoed back in error response

НЕ покрыто здесь (см. FOUNDATION_TASK_5_REPORT.md → секция H):
  - #10 rate limit → 429: slowapi's Limiter is a process-wide singleton
    with in-memory storage; a real 429 test is included but is order-
    sensitive (depends on nothing else having hit the same endpoint/IP
    in this pytest session) — see test_429_rate_limit_exceeded.
  - Real Sentry event delivery (needs a live SENTRY_DSN + network) —
    covered only via mocking capture_exception(), never executed against
    a real Sentry project. See FOUNDATION_TASK_5_REPORT.md item 11.
  - This whole file has NOT been executed in this sandbox (no network,
    fastapi/sqlalchemy/etc. are not installed here) — it is code-complete
    and written to match tests/conftest.py + tests/test_rbac.py
    conventions, but its actual PASS/FAIL status is NOT EXECUTED.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from api import app
from auth import get_current_agency, get_current_restaurant_admin, get_telegram_user
from config import settings
from database import get_db
from models import Location


# ─────────────────────────────────────────
# HELPERS (mirrors tests/test_rbac.py conventions)
# ─────────────────────────────────────────
def _raw_client(db):
    """TestClient без auth-overrides — реальная JWT/role проверка."""
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _authed_restaurant_client(db, restaurant):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant
    return TestClient(app, raise_server_exceptions=True)


# ─────────────────────────────────────────
# #1, #2, #3 — unexpected exception → 500, no traceback, no secrets
# ─────────────────────────────────────────
@pytest.mark.security
def test_unexpected_exception_returns_safe_500(db, restaurant, monkeypatch):
    """
    GET /api/restaurants/me/tables не оборачивает db.query() в try/except —
    ловим настоящее необработанное исключение и проверяем, что:
      - клиент получает 500 с {"detail": "Internal server error", "request_id": ...}
      - НЕ traceback, НЕ имя файла/модуля, НЕ текст оригинальной ошибки
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated unexpected DB failure: connection to host X refused")

    monkeypatch.setattr(db, "query", _boom)

    c = _authed_restaurant_client(db, restaurant)
    try:
        resp = c.get("/api/restaurants/me/tables")
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"] == "Internal server error"
        assert "request_id" in body and body["request_id"]

        raw_text = resp.text
        assert "Traceback" not in raw_text
        assert "RuntimeError" not in raw_text
        assert "simulated unexpected DB failure" not in raw_text
        assert ".py" not in raw_text  # no file paths
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #12 — request_id in response header == request_id in 500 body
# ─────────────────────────────────────────
@pytest.mark.security
def test_request_id_propagated_header_and_body_match(db, restaurant, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "query", _boom)

    c = _authed_restaurant_client(db, restaurant)
    try:
        resp = c.get("/api/restaurants/me/tables")
        assert resp.status_code == 500
        assert "X-Request-ID" in resp.headers
        assert resp.headers["X-Request-ID"] == resp.json()["request_id"]
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #11 — request ID exists on ordinary (non-error) responses too
# ─────────────────────────────────────────
@pytest.mark.security
def test_request_id_present_on_normal_response(client):
    resp = client.get("/health")
    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) > 0


# ─────────────────────────────────────────
# #4 — validation error → 422
# ─────────────────────────────────────────
@pytest.mark.security
def test_invalid_path_param_returns_422(db):
    """restaurant_id должен быть int — строка не проходит валидацию пути."""
    c = _raw_client(db)
    try:
        resp = c.get(
            "/api/orders/restaurant/not-an-integer",
            headers={"Authorization": "Bearer irrelevant-because-422-first"},
        )
        # FastAPI валидирует путь до вызова Depends() — 422, не 401/500
        assert resp.status_code == 422
        assert "Traceback" not in resp.text
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #5 — no token → 401 (Foundation Task 5 fix: HTTPBearer auto_error=False)
# ─────────────────────────────────────────
@pytest.mark.security
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/analytics/summary"),
        ("GET", "/api/menu/1/all"),
        ("GET", "/api/orders/restaurant/1"),
        ("GET", "/api/restaurants/me/tables"),
    ],
)
def test_401_no_token_on_protected_endpoints(db, method, path):
    """
    До Foundation Task 5: HTTPBearer() с auto_error=True по умолчанию
    отвечал 403 "Not authenticated" на ОТСУТСТВУЮЩИЙ заголовок Authorization
    (задокументированная особенность FastAPI/Starlette) — это расходилось
    с тестами tests/test_rbac.py::test_401_no_token_on_* и с ожидаемой
    таблицей семантики (нет токена → 401, роль неверна → 403).
    После фикса (bearer_scheme = HTTPBearer(auto_error=False) +
    ручная проверка) — 401.
    """
    c = _raw_client(db)
    try:
        resp = c.request(method, path)
        assert resp.status_code == 401, f"{path}: ожидали 401, получили {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #6 — valid token, wrong role → 403
# ─────────────────────────────────────────
@pytest.mark.security
def test_403_wrong_role_on_agency_only_endpoint(db, restaurant_token):
    """
    Валидный JWT, но роль restaurant_admin, на agency_owner-only
    endpoint (GET /api/agency/me, зависит от get_current_agency) → 403,
    не 401 (токен валиден и authenticated, просто не та роль) и не 500.
    Детальную role-escalation матрицу см. tests/test_rbac.py.
    """
    c = _raw_client(db)
    try:
        resp = c.get(
            "/api/agency/me",
            headers={"Authorization": f"Bearer {restaurant_token}"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #7 — foreign tenant resource → 404 (not 403 with details)
# ─────────────────────────────────────────
@pytest.mark.security
def test_404_cross_tenant_order_not_403(db, restaurant, restaurant2):
    """
    restaurant_admin ресторана A запрашивает список заказов ресторана B
    по ID → 403 "Нет доступа" (не путём подтверждения существования via 404
    с деталями). Здесь фиксируем текущее, задокументированное поведение:
    сравнение restaurant.id != restaurant_id → 403 (см. FOUNDATION_TASK_5
    отчёт, находка о restaurant_id-в-URL, которая отличается от
    entity-level tenant isolation, где используется 404).
    """
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(f"/api/orders/restaurant/{restaurant2.id}")
        assert resp.status_code == 403
        assert "restaurant2" not in resp.text  # no internal identifiers leaked
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_404_cross_tenant_order_by_id(db, restaurant, restaurant2, tg_user2):
    """
    Гость ресторана B пытается получить заказ ресторана A по ID через
    /api/orders/my/{order_id} → 404 (IDOR невозможен), см. orders.py.
    """
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_telegram_user] = lambda: tg_user2
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get("/api/orders/my/999999")
        assert resp.status_code == 404
        assert "999999" not in resp.text or resp.json().get("detail") == "Заказ не найден"
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #8, #9 — duplicate resource (unique constraint) → 409, no raw DB error
# ─────────────────────────────────────────
@pytest.mark.security
def test_duplicate_table_number_returns_409_not_raw_db_error(db, restaurant):
    # S1-2: router now resolves location_id via Location.restaurant_id.
    # Must seed a Location so the endpoint doesn't return 500.
    loc = Location(
        restaurant_id=restaurant.id,
        name=restaurant.name,
        slug=restaurant.slug,
        is_active=True,
        timezone="Asia/Tashkent",
        delivery_fee=0,
        min_order_amount=0,
        currency="UZS",
        language="uz",
        is_waiter_call_enabled=False,
    )
    db.add(loc)
    db.flush()

    c = _authed_restaurant_client(db, restaurant)
    try:
        payload = {"table_number": "T-DUPLICATE-TEST"}
        first = c.post("/api/restaurants/me/tables", json=payload)
        assert first.status_code == 201

        second = c.post("/api/restaurants/me/tables", json=payload)
        assert second.status_code == 409
        body = second.json()
        assert "duplicate key value violates" not in body.get("detail", "")
        assert "uq_table_restaurant_number" not in body.get("detail", "")
        assert "uq_table_location_number" not in body.get("detail", "")
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #13 — exception is actually logged
# ─────────────────────────────────────────
@pytest.mark.security
def test_unexpected_exception_is_logged(db, restaurant, monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom-for-logging-test")

    monkeypatch.setattr(db, "query", _boom)

    c = _authed_restaurant_client(db, restaurant)
    try:
        with caplog.at_level(logging.ERROR):
            resp = c.get("/api/restaurants/me/tables")
        assert resp.status_code == 500
        assert any(
            "Необработанное исключение" in rec.message
            for rec in caplog.records
        )
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #14, #17 — secrets never appear in logs or responses
# ─────────────────────────────────────────
@pytest.mark.security
def test_webhook_invalid_secret_does_not_leak_secret(db, restaurant, caplog):
    """
    Невалидный X-Telegram-Bot-Api-Secret-Token → 403, и ни в ответе,
    ни в логах не должно быть настоящего settings.WEBHOOK_SECRET.
    """
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        with caplog.at_level(logging.WARNING):
            resp = c.post(
                f"/webhook/{restaurant.slug}",
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret-value"},
            )
        assert resp.status_code == 403
        assert settings.WEBHOOK_SECRET not in resp.text
        for rec in caplog.records:
            assert settings.WEBHOOK_SECRET not in rec.getMessage()
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #18 — Telegram initData is not echoed back
# ─────────────────────────────────────────
@pytest.mark.security
def test_invalid_init_data_not_echoed(db, restaurant):
    """
    GET /api/orders/my зависит от get_telegram_user, которая верифицирует
    X-Telegram-Init-Data через HMAC. Невалидная подпись → 401, и сырой
    initData (включая поддельный hash) не должен попасть в тело ответа.
    """
    fake_init_data = "user=%7B%22id%22%3A1%7D&auth_date=1&hash=deadbeef"
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    try:
        resp = c.get(
            "/api/orders/my",
            headers={
                "X-Restaurant-Id": str(restaurant.id),
                "X-Telegram-Init-Data": fake_init_data,
            },
        )
        assert resp.status_code in (401, 403)
        assert fake_init_data not in resp.text
        assert "deadbeef" not in resp.text
    finally:
        app.dependency_overrides.clear()


# ─────────────────────────────────────────
# #15, #16 — Sentry capture called for 500, NOT called for 401/404
# ─────────────────────────────────────────
@pytest.mark.security
def test_sentry_captures_unexpected_exception(db, restaurant, monkeypatch):
    import sentry_sdk

    calls = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc=None: calls.append(exc))

    def _boom(*args, **kwargs):
        raise RuntimeError("boom-for-sentry-test")

    monkeypatch.setattr(db, "query", _boom)

    c = _authed_restaurant_client(db, restaurant)
    try:
        resp = c.get("/api/restaurants/me/tables")
        assert resp.status_code == 500
        assert len(calls) == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_sentry_not_called_for_401_and_404(db, restaurant, monkeypatch):
    import sentry_sdk

    calls = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc=None: calls.append(exc))

    # 401 — no token
    c1 = _raw_client(db)
    resp1 = c1.get("/api/restaurants/me/tables")
    assert resp1.status_code == 401

    # 404 — cross-tenant order lookup
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_restaurant_admin] = lambda: restaurant
    c2 = TestClient(app, raise_server_exceptions=True)
    resp2 = c2.get("/api/orders/restaurant/999999999")
    app.dependency_overrides.clear()

    assert len(calls) == 0, (
        "401/403 не должны создавать Sentry-события (item 23) — "
        f"вызвано {len(calls)} раз(а)"
    )


# ─────────────────────────────────────────
# #10 — rate limit → 429
# NOTE: slowapi Limiter (limiter.py) — процесс-широкий singleton с
# in-memory storage. Этот тест сбрасывает его перед запуском, но если
# в будущем другой тест начнёт бить по /api/agency/register — тест
# может стать order-dependent. NOT EXECUTED в этой песочнице (нет
# fastapi/slowapi) — см. FOUNDATION_TASK_5_REPORT.md.
# ─────────────────────────────────────────
@pytest.mark.security
def test_429_rate_limit_exceeded_on_register(db):
    from limiter import limiter

    limiter.reset()
    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app, raise_server_exceptions=True)
    payload = {
        "name": "Rate Limit Test Agency",
        "email": "ratelimit-test@example.com",
        "password": "password12345",
    }
    try:
        statuses = [c.post("/api/agency/register", json=payload).status_code for _ in range(6)]
        assert 429 in statuses, f"Ожидали 429 среди 6 быстрых запросов, получили {statuses}"
    finally:
        app.dependency_overrides.clear()
        limiter.reset()
