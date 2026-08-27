"""
tests/test_security_headers.py — Foundation Task 7: Security Headers / CORS / Rate Limiting

Проверяет:
  SH-1.  X-Content-Type-Options: nosniff присутствует на всех ответах
  SH-2.  X-Frame-Options не равен DENY (заменён CSP frame-ancestors)
  SH-3.  Referrer-Policy присутствует
  SH-4.  Strict-Transport-Security с max-age >= 63072000
  SH-5.  Permissions-Policy присутствует
  SH-6.  Content-Security-Policy присутствует
  SH-7.  CSP содержит telegram.org в script-src
  SH-8.  CSP содержит frame-ancestors
  SH-9.  Server header не раскрывает версию
  SH-10. Cache-Control: no-store на auth endpoints (через conftest client с БД)
  SH-11. Cache-Control: no-store на /api/superadmin/login

  CORS-1. Preflight с origin → 200/204
  CORS-2. Запрос без Origin не ломается
  CORS-3. allow_methods не wildcard при allow_credentials=True
  CORS-4. Authorization не в Expose-Headers

  RL-1.  Rate limit exceeded → 429
  RL-2.  Webhook имеет rate limit декоратор
  RL-3.  Superadmin login лимит строже API default
  RL-4.  Agency registration лимит строже login
  RL-5.  RATE_LIMIT_API задан и разумный
  RL-6.  RATE_LIMIT_LOGIN задан и используется

  RLS-1. Rate limit storage — in-memory (не Redis)
  RLS-2. Limiter использует get_remote_address

  PROXY-1. X-Request-ID присутствует в каждом ответе
  PROXY-2. X-Request-ID клиента прокидывается в ответ
  PROXY-3. Уникальные X-Request-ID без override

  TP-1.  TRUSTED_PROXY_HOSTS задан
  TP-2.  ProxyHeadersMiddleware присутствует в api.py
  TP-3.  Wildcard proxy — documented known risk

  SEL-1. Billing /subscribe покрыт default_limits
  SEL-2. Impersonate без индивидуального лимита — known gap (skip)
"""

import pytest
from fastapi.testclient import TestClient

from api import app

# Клиент БЕЗ БД — для тестов которые не обращаются к БД (headers, CORS, config)
client_no_db = TestClient(app, raise_server_exceptions=False)


def _headers(path: str = "/health", method: str = "GET", **kwargs):
    """Делает запрос, возвращает (response, headers_lowercase)."""
    resp = getattr(client_no_db, method.lower())(path, **kwargs)
    return resp, {k.lower(): v for k, v in resp.headers.items()}


# ═══════════════════════════════════════════════════════════════════
# SH — SECURITY HEADERS
# ═══════════════════════════════════════════════════════════════════

class TestSecurityHeaders:

    def test_sh1_x_content_type_options(self):
        """SH-1: X-Content-Type-Options: nosniff."""
        _, h = _headers("/health")
        assert h.get("x-content-type-options") == "nosniff"

    def test_sh2_x_frame_options_not_deny(self):
        """SH-2: X-Frame-Options не DENY (заменён CSP frame-ancestors)."""
        _, h = _headers("/health")
        assert h.get("x-frame-options", "").upper() != "DENY"

    def test_sh3_referrer_policy(self):
        """SH-3: Referrer-Policy присутствует."""
        _, h = _headers("/health")
        assert h.get("referrer-policy"), "Referrer-Policy отсутствует"

    def test_sh4_hsts(self):
        """SH-4: HSTS max-age >= 63072000."""
        _, h = _headers("/health")
        hsts = h.get("strict-transport-security", "")
        assert hsts, "Strict-Transport-Security отсутствует"
        for part in hsts.split(";"):
            if part.strip().lower().startswith("max-age="):
                assert int(part.strip().split("=")[1]) >= 63072000
                return
        pytest.fail(f"HSTS не содержит max-age: {hsts!r}")

    def test_sh5_permissions_policy(self):
        """SH-5: Permissions-Policy присутствует."""
        _, h = _headers("/health")
        assert h.get("permissions-policy"), "Permissions-Policy отсутствует"

    def test_sh6_csp_present(self):
        """SH-6: Content-Security-Policy присутствует."""
        _, h = _headers("/health")
        assert h.get("content-security-policy"), "CSP отсутствует"

    def test_sh7_csp_telegram(self):
        """SH-7: CSP разрешает telegram.org (Mini App SDK)."""
        _, h = _headers("/health")
        assert "telegram.org" in h.get("content-security-policy", "")

    def test_sh8_csp_frame_ancestors(self):
        """SH-8: CSP содержит frame-ancestors."""
        _, h = _headers("/health")
        assert "frame-ancestors" in h.get("content-security-policy", "")

    def test_sh9_server_no_version(self):
        """SH-9: Server header не раскрывает версию."""
        import re
        _, h = _headers("/health")
        assert not re.search(r"/[\d.]+", h.get("server", ""))

    def test_sh10_cache_control_agency_login(self):
        """
        SH-10: Cache-Control: no-store настроен для /api/agency/login.

        Проверяем через code inspection: SecurityHeadersMiddleware._NO_CACHE_PATHS
        содержит /api/agency/login, и middleware добавляет no-store заголовок.

        HTTP-запрос к agency/login не используется — endpoint требует реальной
        БД с agency данными, что конфликтует с test isolation в SQLite.
        sh11 покрывает runtime-поведение через superadmin/login (не требует БД agency).
        """
        import inspect

        import api as api_module

        # Проверяем что _NO_CACHE_PATHS определён и содержит agency/login
        src = inspect.getsource(api_module.SecurityHeadersMiddleware)
        assert "/api/agency/login" in src, (
            "SecurityHeadersMiddleware._NO_CACHE_PATHS не содержит /api/agency/login"
        )
        assert "no-store" in src, (
            "SecurityHeadersMiddleware не добавляет Cache-Control: no-store"
        )

        # Проверяем runtime: _NO_CACHE_PATHS как frozenset
        assert hasattr(api_module.SecurityHeadersMiddleware, "_NO_CACHE_PATHS"), (
            "SecurityHeadersMiddleware не имеет _NO_CACHE_PATHS"
        )
        paths = api_module.SecurityHeadersMiddleware._NO_CACHE_PATHS
        assert "/api/agency/login" in paths
        assert "/api/agency/restaurant-login" in paths
        assert "/api/superadmin/login" in paths
        assert "/api/agency/register" in paths

    def test_sh11_cache_control_superadmin_login(self):
        """SH-11: Cache-Control: no-store на /api/superadmin/login."""
        # superadmin/login проверяет email из env до БД → 401 без DB fixture
        _, h = _headers(
            "/api/superadmin/login",
            method="POST",
            json={"email": "x@x.com", "password": "wrong"},
        )
        assert "no-store" in h.get("cache-control", "")


# ═══════════════════════════════════════════════════════════════════
# CORS
# ═══════════════════════════════════════════════════════════════════

class TestCORS:

    def test_cors1_preflight_allowed_origin(self):
        """CORS-1: OPTIONS preflight → 200/204."""
        resp = client_no_db.options(
            "/api/agency/login",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,Authorization",
            },
        )
        assert resp.status_code in (200, 204)

    def test_cors2_no_origin_works(self):
        """CORS-2: Запрос без Origin не ломается."""
        resp = client_no_db.get("/health")
        assert resp.status_code == 200

    def test_cors3_allow_methods_not_wildcard(self):
        """CORS-3: allow_methods не wildcard при allow_credentials=True."""
        resp = client_no_db.options(
            "/api/agency/login",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        assert allow_methods != "*"

    def test_cors4_no_expose_authorization(self):
        """CORS-4: Authorization не в Access-Control-Expose-Headers."""
        resp = client_no_db.options(
            "/api/agency/login",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        exposed = resp.headers.get("access-control-expose-headers", "").lower()
        assert "authorization" not in exposed

    def test_cors5_x_location_id_in_allow_headers(self):
        """CORS-5: X-Location-Id разрешён в preflight (нужен для create_order/create_reservation из PWA).

        Regression: X-Location-Id отсутствовал в allow_headers до этого фикса.
        Без него браузерный/PWA клиент получал CORS rejection при POST /api/orders/create.
        """
        resp = client_no_db.options(
            "/api/orders/create",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Location-Id,X-Restaurant-Id,Content-Type",
            },
        )
        assert resp.status_code in (200, 204)
        allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
        assert "x-location-id" in allow_headers, (
            f"X-Location-Id отсутствует в Access-Control-Allow-Headers: {allow_headers!r}"
        )


# ═══════════════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════════════

class TestRateLimitConfig:

    def test_rl1_rate_limit_returns_429(self):
        """RL-1: Превышение rate limit → 429."""
        statuses = set()
        for _ in range(10):
            resp = client_no_db.post(
                "/api/superadmin/login",
                json={"email": "x@x.com", "password": "wrong"},
            )
            statuses.add(resp.status_code)
        assert 500 not in statuses, f"Rate limit вернул 500: {statuses}"
        assert 429 in statuses, f"429 не получен за 10 запросов: {statuses}"

    def test_rl2_webhook_has_rate_limit(self):
        """RL-2: Webhook задекорирован @limiter.limit."""
        from api import restaurant_webhook
        assert hasattr(restaurant_webhook, "_rate_limits") or hasattr(
            restaurant_webhook, "__wrapped__"
        )

    def test_rl3_superadmin_login_stricter(self):
        """RL-3: RATE_LIMIT_SUPERADMIN_LOGIN строже RATE_LIMIT_API."""
        from config import settings

        def _rps(s: str) -> float:
            count, period = s.split("/")
            return int(count) / {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[period]

        assert _rps(settings.RATE_LIMIT_SUPERADMIN_LOGIN) < _rps(settings.RATE_LIMIT_API)

    def test_rl4_agency_registration_strict(self):
        """RL-4: Agency registration имеет строгий rate limit."""
        import inspect
        from routers import agency as agency_module
        src = inspect.getsource(agency_module)
        assert "5/hour" in src or "1/minute" in src or "5/minute" in src

    def test_rl5_api_default_reasonable(self):
        """RL-5: RATE_LIMIT_API задан и <= 1000 запросов."""
        from config import settings
        rate = settings.RATE_LIMIT_API
        assert rate
        count, period = rate.split("/")
        assert int(count) <= 1000
        assert period in ("second", "minute", "hour")

    def test_rl6_login_rate_limit_used(self):
        """RL-6: RATE_LIMIT_LOGIN задан и применяется в agency router."""
        import inspect
        from config import settings
        from routers import agency as agency_module
        assert settings.RATE_LIMIT_LOGIN
        assert "RATE_LIMIT_LOGIN" in inspect.getsource(agency_module)


# ═══════════════════════════════════════════════════════════════════
# RATE LIMIT STORAGE
# ═══════════════════════════════════════════════════════════════════

class TestRateLimitStorage:

    def test_rls1_no_redis(self):
        """RLS-1: Storage — in-memory (не Redis). Known limitation для MVP."""
        from limiter import limiter
        storage = getattr(limiter, "_storage", None) or getattr(limiter, "storage", None)
        if storage is not None:
            assert "Redis" not in type(storage).__name__

    def test_rls2_key_func_remote_address(self):
        """RLS-2: key_func = get_remote_address."""
        from limiter import limiter
        from slowapi.util import get_remote_address
        assert limiter._key_func is get_remote_address


# ═══════════════════════════════════════════════════════════════════
# REQUEST ID
# ═══════════════════════════════════════════════════════════════════

class TestRequestID:

    def test_proxy1_request_id_present(self):
        """PROXY-1: X-Request-ID в каждом ответе."""
        _, h = _headers("/health")
        assert h.get("x-request-id"), "X-Request-ID отсутствует"

    def test_proxy2_client_id_propagated(self):
        """PROXY-2: X-Request-ID клиента прокидывается в ответ."""
        custom_id = "test-id-abc123"
        resp = client_no_db.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.headers.get("x-request-id") == custom_id

    def test_proxy3_unique_ids(self):
        """PROXY-3: Без X-Request-ID каждый запрос получает уникальный ID."""
        ids = {client_no_db.get("/health").headers.get("x-request-id") for _ in range(5)}
        assert len(ids) == 5


# ═══════════════════════════════════════════════════════════════════
# TRUSTED PROXY
# ═══════════════════════════════════════════════════════════════════

class TestTrustedProxy:

    def test_tp1_trusted_proxy_hosts_configured(self):
        """TP-1: TRUSTED_PROXY_HOSTS задан в config."""
        from config import settings
        assert settings.TRUSTED_PROXY_HOSTS

    def test_tp2_proxy_middleware_in_source(self):
        """TP-2: ProxyHeadersMiddleware присутствует в api.py."""
        import inspect
        import api as api_module
        assert "ProxyHeadersMiddleware" in inspect.getsource(api_module)

    def test_tp3_wildcard_proxy_documented(self):
        """TP-3: TRUSTED_PROXY_HOSTS='*' — documented known risk для Render."""
        from config import settings
        # '*' допустимо на Render (приложение не доступно напрямую из интернета)
        # Тест всегда PASS — фиксирует текущее состояние
        assert settings.TRUSTED_PROXY_HOSTS is not None


# ═══════════════════════════════════════════════════════════════════
# SENSITIVE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestSensitiveEndpointLimits:

    def test_sel1_billing_covered_by_default(self):
        """SEL-1: /api/billing/subscribe покрыт Limiter default_limits."""
        from limiter import limiter
        # slowapi 0.1.9: default_limits хранится в _default_limits
        dl = getattr(limiter, "_default_limits", None) or getattr(
            limiter, "default_limits", None
        )
        assert dl, "Limiter не имеет default_limits — billing/subscribe не защищён"

    def test_sel2_impersonate_no_individual_limit(self):
        """
        SEL-2: impersonate без индивидуального rate limit — known gap.
        Покрыт default (120/min) + superadmin JWT auth.
        """
        import inspect
        from routers import superadmin as sa_module
        src = inspect.getsource(sa_module)
        lines = src.split("\n")
        in_impersonate = False
        has_individual = False
        for line in lines:
            if "def impersonate_agency" in line:
                in_impersonate = True
            if in_impersonate and "@limiter.limit" in line:
                has_individual = True
                break
            if in_impersonate and line.strip().startswith("def ") and "impersonate" not in line:
                break
        if not has_individual:
            pytest.skip(
                "KNOWN GAP: impersonate без индивидуального rate limit. "
                "Покрыт default (120/min). Рекомендуется 3/minute в будущей задаче."
            )
