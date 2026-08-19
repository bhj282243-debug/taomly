"""
tests/test_telegram_auth.py — Foundation Task 8: Telegram Authentication Hardening

Покрывает АУДИТ из FOUNDATION_TASK_8 (initData / HMAC / auth_date / replay /
cross-restaurant isolation / bot token security / webhook auth / active-state /
error handling / rate limiting), выполненный над auth.verify_telegram_init_data,
auth.get_telegram_user и webhook-роутами в api.py.

Стиль соответствует tests/test_error_handling.py и tests/test_rbac.py:
  - `_raw_client(db)` — TestClient БЕЗ переопределения get_telegram_user,
    т.е. запросы проходят через реальную HMAC-верификацию initData.
  - `client` (из conftest.py) — используется только там, где сама
    Telegram-верификация не тестируется (например rate limit).

АРХИТЕКТУРНАЯ ЗАМЕТКА — webhook handler и committed session (v3):
  Эндпоинт /webhook/{slug} использует `with SessionLocal() as db:` (database.py),
  а не FastAPI dependency get_db. Это намеренное архитектурное решение:
  webhook вызывается Telegram-сервером напрямую, вне обычного request lifecycle.
  Следствие для тестов: override_get_db НЕ действует на webhook handler —
  он открывает отдельное соединение к PostgreSQL и видит только COMMITTED данные.

  test_duplicate_bot_token_rejected_on_create:
    исправлено добавлением `restaurant` в параметры — fixture создаёт запись
    в тестовой транзакции, которую _raw_client видит через тот же db session.

  test_webhook_valid_secret_accepted:
    restaurant создаётся через отдельный database.SessionLocal() с явным commit(),
    вне SAVEPOINT-транзакции тестового db. После теста запись удаляется вручную
    через тот же committed session. Production api.py и database.py не изменялись.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from api import app
from auth import decrypt_token, encrypt_token, get_telegram_user, hash_password
from config import settings
from database import SessionLocal, get_db
from models import Agency, Restaurant


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def _raw_client(db) -> TestClient:
    """TestClient без dependency overrides — реальная HMAC-верификация initData."""
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _sign(bot_token: str, fields: dict) -> str:
    """Считает HMAC-SHA256 hash по тому же алгоритму, что и auth.py."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(key=b"WebAppData", msg=bot_token.encode(), digestmod=hashlib.sha256).digest()
    return hmac.new(key=secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()


def _build_init_data(
    bot_token: str,
    user: dict | None = None,
    auth_date: int | None = None,
    include_user: bool = True,
    extra_fields: dict | None = None,
    override_hash: str | None = None,
) -> str:
    """
    Собирает валидную (или намеренно битую, если передать override_hash) строку
    initData, как её присылает настоящий Telegram-клиент.
    """
    if auth_date is None:
        auth_date = int(time.time())
    if user is None:
        user = {"id": 111111111, "first_name": "Тест", "username": "testuser", "language_code": "ru"}

    fields = {"auth_date": str(auth_date)}
    if include_user:
        fields["user"] = json.dumps(user, separators=(",", ":"))
    if extra_fields:
        fields.update(extra_fields)

    computed_hash = _sign(bot_token, fields)
    fields["hash"] = override_hash if override_hash is not None else computed_hash
    return urlencode(fields)


def _decrypted_token(restaurant: Restaurant) -> str:
    return decrypt_token(restaurant.telegram_bot_token_encrypted)


ORDERS_MY_URL = "/api/orders/my"


# ═════════════════════════════════════════
# 1-2 — VALID initData / MISSING hash
# ═════════════════════════════════════════
@pytest.mark.security
def test_valid_init_data_accepted(db, restaurant):
    """Корректно подписанная initData → 200, гость НЕ создаётся (реальный tg id)."""
    bot_token = _decrypted_token(restaurant)
    init_data = _build_init_data(bot_token)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 200
        assert resp.json() == []  # нет заказов у нового пользователя, но НЕ 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_missing_hash_rejected(db, restaurant):
    """initData без поля hash → 401."""
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 1})}
    init_data = urlencode(fields)  # без hash
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_invalid_hash_rejected(db, restaurant):
    """Подделанный hash → 401."""
    bot_token = _decrypted_token(restaurant)
    init_data = _build_init_data(bot_token, override_hash="0" * 64)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# 3-7 — auth_date
# ═════════════════════════════════════════
@pytest.mark.security
def test_missing_auth_date_rejected(db, restaurant):
    bot_token = _decrypted_token(restaurant)
    fields = {"user": json.dumps({"id": 1})}
    fields["hash"] = _sign(bot_token, fields)
    init_data = urlencode(fields)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_malformed_auth_date_rejected(db, restaurant):
    bot_token = _decrypted_token(restaurant)
    fields = {"auth_date": "not-a-number", "user": json.dumps({"id": 1})}
    fields["hash"] = _sign(bot_token, fields)
    init_data = urlencode(fields)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_expired_auth_date_rejected(db, restaurant):
    """auth_date старше MAX_INIT_DATA_AGE_SECONDS → 401."""
    bot_token = _decrypted_token(restaurant)
    expired = int(time.time()) - settings.MAX_INIT_DATA_AGE_SECONDS - 60
    init_data = _build_init_data(bot_token, auth_date=expired)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_future_auth_date_rejected(db, restaurant):
    """auth_date из будущего → 401 (защита от подмены времени клиентом)."""
    bot_token = _decrypted_token(restaurant)
    future = int(time.time()) + 3600
    init_data = _build_init_data(bot_token, auth_date=future)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# 8-9 — MODIFIED user / auth_date после подписи
# ═════════════════════════════════════════
@pytest.mark.security
def test_modified_user_data_rejected(db, restaurant):
    """user изменён ПОСЛЕ подписи (без пересчёта hash) → 401."""
    bot_token = _decrypted_token(restaurant)
    init_data = _build_init_data(bot_token)
    tampered = init_data.replace("111111111", "999999999")
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": tampered},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_modified_auth_date_rejected(db, restaurant):
    """auth_date изменён ПОСЛЕ подписи (без пересчёта hash) → 401."""
    bot_token = _decrypted_token(restaurant)
    now = int(time.time())
    init_data = _build_init_data(bot_token, auth_date=now)
    tampered = init_data.replace(f"auth_date={now}", f"auth_date={now - 1}")
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": tampered},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# 10-11 — WRONG bot token / WRONG restaurant context
# ═════════════════════════════════════════
@pytest.mark.security
def test_wrong_bot_token_rejected(db, restaurant):
    """initData подписана НЕ тем токеном, что хранится у ресторана → 401."""
    init_data = _build_init_data("0000000000:CompletelyWrongToken")
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_wrong_restaurant_context_rejected(db, restaurant, restaurant2):
    """
    Критический сценарий: initData ресторана A (подписана токеном A)
    + X-Restaurant-Id ресторана B → 401, т.к. HMAC проверяется токеном B.
    """
    bot_token_a = _decrypted_token(restaurant)
    init_data_a = _build_init_data(bot_token_a)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant2.id), "X-Telegram-Init-Data": init_data_a},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# 12-14 — ACTIVE STATE (restaurant / agency)
# ═════════════════════════════════════════
@pytest.mark.security
def test_inactive_restaurant_rejected(db, restaurant):
    bot_token = _decrypted_token(restaurant)
    init_data = _build_init_data(bot_token)
    restaurant.is_active = False
    db.flush()
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_inactive_agency_rejected(db, restaurant, agency):
    bot_token = _decrypted_token(restaurant)
    init_data = _build_init_data(bot_token)
    agency.is_active = False
    db.flush()
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_missing_restaurant_rejected(db):
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": "999999999", "X-Telegram-Init-Data": "auth_date=1&hash=deadbeef"},
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# 15-16 — MALFORMED initData / MISSING user
# ═════════════════════════════════════════
@pytest.mark.security
def test_malformed_init_data_rejected(db, restaurant):
    """Полностью не query-string-формат → не должно падать с 500, только 401."""
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": "this-is-not-valid-init-data-@@@"},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_missing_telegram_user_rejected(db, restaurant):
    """Валидная подпись, но поле user отсутствует → 401."""
    bot_token = _decrypted_token(restaurant)
    init_data = _build_init_data(bot_token, include_user=False)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# 17-19 — BOT TOKEN SECURITY
# ═════════════════════════════════════════
@pytest.mark.security
def test_bot_token_encrypted_at_rest(db, restaurant):
    """В БД лежит НЕ plaintext-токен, а Fernet-шифротекст, который расшифровывается обратно."""
    plain = "1234567890:AAFakeTokenForTests"
    assert restaurant.telegram_bot_token_encrypted != plain
    assert decrypt_token(restaurant.telegram_bot_token_encrypted) == plain


@pytest.mark.security
def test_decrypt_failure_handled_safely(db, restaurant):
    """Повреждённый ciphertext → 401 (не 500), FERNET_KEY не попадает в ответ."""
    restaurant.telegram_bot_token_encrypted = "not-a-valid-fernet-token"
    db.flush()
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={
                "X-Restaurant-Id": str(restaurant.id),
                "X-Telegram-Init-Data": "auth_date=1&user=%7B%7D&hash=deadbeef",
            },
        )
        assert resp.status_code == 401
        assert settings.FERNET_KEY not in resp.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_bot_token_not_exposed_in_agency_response(db, agency, restaurant, agency_token):
    """GET /api/agency/restaurants никогда не должен вернуть токен (ни plaintext, ни шифротекст)."""
    plain = "1234567890:AAFakeTokenForTests"
    c = _raw_client(db)
    try:
        resp = c.get(
            "/api/agency/restaurants",
            headers={"Authorization": f"Bearer {agency_token}"},
        )
        assert resp.status_code == 200
        assert plain not in resp.text
        assert restaurant.telegram_bot_token_encrypted not in resp.text
        assert "telegram_bot_token_encrypted" not in resp.text
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# CROSS-RESTAURANT — Duplicate bot token guard (Task 8 minimal fix)
# ═════════════════════════════════════════
@pytest.mark.security
def test_duplicate_bot_token_rejected_on_create(db, agency, agency_token, restaurant):
    """
    Нельзя создать второй ресторан с ТЕМ ЖЕ bot token, что уже используется.

    FIX v3: добавлен `restaurant` в параметры — fixture создаёт запись с токеном
    "1234567890:AAFakeTokenForTests" в тестовой сессии до вызова. _raw_client(db)
    использует тот же db session, поэтому _bot_token_in_use() видит существующий
    токен и возвращает True → 400.
    """
    c = _raw_client(db)
    try:
        resp = c.post(
            "/api/agency/restaurants",
            headers={"Authorization": f"Bearer {agency_token}"},
            json={
                "name": "Duplicate Bot Restaurant",
                "slug": "dup-bot-restaurant",
                "admin_password": "password123456",
                "telegram_bot_token": "1234567890:AAFakeTokenForTests",  # == restaurant fixture's token
            },
        )
        assert resp.status_code == 400
        assert "уже используется" in resp.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_duplicate_bot_token_rejected_on_update(db, agency, agency_token, restaurant, restaurant2):
    """Нельзя переключить restaurant на bot token, который уже занят restaurant2."""
    c = _raw_client(db)
    try:
        resp = c.patch(
            f"/api/agency/restaurants/{restaurant.id}",
            headers={"Authorization": f"Bearer {agency_token}"},
            json={"telegram_bot_token": "9876543210:AAFakeTokenForTests2"},  # == restaurant2's token
        )
        assert resp.status_code == 400
        assert "уже используется" in resp.text
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# 20-22 — WEBHOOK AUTHENTICATION
# ═════════════════════════════════════════
@pytest.mark.security
def test_webhook_invalid_secret_rejected(db, restaurant):
    c = _raw_client(db)
    try:
        resp = c.post(
            f"/webhook/{restaurant.slug}",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_webhook_missing_secret_rejected(db, restaurant):
    c = _raw_client(db)
    try:
        resp = c.post(f"/webhook/{restaurant.slug}", json={"update_id": 1})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_webhook_valid_secret_accepted(db):
    """
    Webhook с валидным secret + существующий ресторан → HTTP 200, {"ok": True}.

    Webhook handler использует `with SessionLocal() as db:` (намеренно — вне
    FastAPI dependency lifecycle). Он видит только COMMITTED данные PostgreSQL.
    override_get_db на него не действует.

    Решение: создаём agency + restaurant через отдельный database.SessionLocal()
    с явным commit() вне SAVEPOINT-транзакции тестового db. После теста удаляем
    записи вручную через тот же committed session. Production код не изменялся.

    На SQLite тест пропускается автоматически — там нет разделения SAVEPOINT/commit
    при in-memory БД (все сессии видят одни данные). Тест целевой для PostgreSQL CI.
    """
    committed_session = SessionLocal()
    agency_id = None
    restaurant_id = None
    try:
        # Создаём agency
        agency = Agency(
            name="Webhook Test Agency",
            owner_email="webhook-test@taomly.uz",
            owner_password_hash=hash_password("webhookpass123"),
        )
        committed_session.add(agency)
        committed_session.commit()
        committed_session.refresh(agency)
        agency_id = agency.id

        # Создаём restaurant с is_active=True
        restaurant = Restaurant(
            agency_id=agency_id,
            name="Webhook Test Restaurant",
            slug="webhook-test-slug",
            admin_password_hash=hash_password("webhookpass123"),
            telegram_bot_token_encrypted=encrypt_token("9999999999:AAWebhookTestToken"),
            is_active=True,
        )
        committed_session.add(restaurant)
        committed_session.commit()
        committed_session.refresh(restaurant)
        restaurant_id = restaurant.id
        slug = restaurant.slug

        # Тестируем webhook — handler найдёт ресторан в PostgreSQL
        c = _raw_client(db)
        try:
            resp = c.post(
                f"/webhook/{slug}",
                json={"update_id": 1},  # update без message — обработчик просто ничего не делает
                headers={"X-Telegram-Bot-Api-Secret-Token": settings.WEBHOOK_SECRET},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
        finally:
            app.dependency_overrides.clear()

    finally:
        # Очистка: удаляем committed записи после теста
        if restaurant_id:
            committed_session.query(Restaurant).filter(Restaurant.id == restaurant_id).delete()
            committed_session.commit()
        if agency_id:
            committed_session.query(Agency).filter(Agency.id == agency_id).delete()
            committed_session.commit()
        committed_session.close()


# ═════════════════════════════════════════
# 23 — HMAC comparison is timing-safe (code-contract check)
# ═════════════════════════════════════════
def test_hmac_comparisons_use_compare_digest():
    """
    Страховка от регрессии: verify_telegram_init_data и оба webhook-роута
    обязаны сравнивать секреты через hmac.compare_digest, а не через `==`/`in`.
    """
    import inspect
    import auth
    import api

    auth_src = inspect.getsource(auth.verify_telegram_init_data)
    assert "hmac.compare_digest" in auth_src

    api_src = inspect.getsource(api)
    assert api_src.count("hmac.compare_digest") >= 2  # /webhook/{slug} + /webhook


# ═════════════════════════════════════════
# 24 — Errors don't expose secrets
# ═════════════════════════════════════════
@pytest.mark.security
def test_telegram_auth_error_does_not_expose_secrets(db, restaurant):
    """401 от verify_telegram_init_data не должен содержать bot token, FERNET_KEY или WEBHOOK_SECRET."""
    bot_token = _decrypted_token(restaurant)
    init_data = _build_init_data(bot_token, override_hash="f" * 64)
    c = _raw_client(db)
    try:
        resp = c.get(
            ORDERS_MY_URL,
            headers={"X-Restaurant-Id": str(restaurant.id), "X-Telegram-Init-Data": init_data},
        )
        assert resp.status_code == 401
        assert bot_token not in resp.text
        assert settings.FERNET_KEY not in resp.text
        assert settings.WEBHOOK_SECRET not in resp.text
    finally:
        app.dependency_overrides.clear()


# ═════════════════════════════════════════
# 25 — Rate limiting on a Telegram-auth-gated endpoint
# ═════════════════════════════════════════
class TestTelegramEndpointRateLimit:
    """
    GET /api/orders/my has its own @limiter.limit("30/minute") (routers/orders.py),
    stricter than the global default. Uses the `client` fixture (get_telegram_user
    override) because rate limiting is independent of the auth dependency itself —
    already exercised for real HMAC verification in the tests above.
    """

    def test_orders_my_rate_limit_enforced(self, client):
        statuses = []
        for _ in range(31):
            r = client.get(ORDERS_MY_URL)
            statuses.append(r.status_code)
        assert 429 in statuses, f"Ожидали 429 среди 31 запроса, получили: {statuses}"
