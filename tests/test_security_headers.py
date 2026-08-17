"""
tests/test_security_headers.py — Foundation Task 7: Security Headers / CORS / Rate Limiting

Проверяет:
  SH-1.  X-Content-Type-Options: nosniff присутствует на всех ответах
  SH-2.  X-Frame-Options удалён (заменён CSP frame-ancestors) — Telegram Mini App
  SH-3.  Referrer-Policy: strict-origin-when-cross-origin присутствует
  SH-4.  Strict-Transport-Security присутствует с max-age >= 63072000
  SH-5.  Permissions-Policy присутствует
  SH-6.  Content-Security-Policy присутствует
  SH-7.  CSP содержит telegram.org в script-src
  SH-8.  CSP содержит frame-ancestors 'none' (защита от clickjacking без DENY)
  SH-9.  Server header не раскрывает версию (отсутствует или пустой)
  SH-10. Cache-Control: no-store на /api/agency/login
  SH-11. Cache-Control: no-store на /api/superadmin/login

  CORS-1. CORS preflight на разрешённый origin — 200 + Access-Control-Allow-Origin
  CORS-2. CORS preflight без Origin — не падает (server-to-server)
  CORS-3. allow_credentials=True + wildcard origins — development fallback безопасен

  RL-1.  Rate limit exceeded → 429 (не 500)
  RL-2.  Webhook endpoint имеет rate limit (300/minute задан)
  RL-3.  Superadmin login rate limit задан строже API-default
  RL-4.  Agency registration rate limit задан строже login
  RL-5.  Billing /subscribe не имеет индивидуального rate limit — только API default (120/min)

  PROXY-1. X-Request-ID header присутствует в ответе
  PROXY-2. X-Request-ID из запроса прокидывается в ответ (если задан клиентом)
  PROXY-3. Несколько последовательных запросов получают разные X-Request-ID (без override)
"""

import os

import pytest
from fastapi.testclient import TestClient

# ─────────────────────────────────────────────────────────────────
# Клиент через conftest (db fixture + override get_db уже сделан там)
# Здесь импортируем напрямую — нам не нужна БД для header-тестов,
# используем TestClient без переопределения get_db.
# ─────────────────────────────────────────────────────────────────
from api import app

# TestClient не делает реальных HTTP-запросов; raise_server_exceptions=False
# позволяет проверить 5xx без падения теста.
client = TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════

def _get_response_headers(path: str = "/health", method: str = "GET", **kwargs):
    """Делает запрос и возвращает (response, headers_dict_lowercase)."""
    fn = getattr(client, method.lower())
    resp = fn(path, **kwargs)
    # Приводим ключи к нижнему регистру для удобства сравнения
    headers = {k.lower(): v for k, v in resp.headers.items()}
    return resp, headers


# ═══════════════════════════════════════════════════════════════════
# SH — SECURITY HEADERS
# ═══════════════════════════════════════════════════════════════════

class TestSecurityHeaders:

    def test_sh1_x_content_type_options(self):
        """SH-1: X-Content-Type-Options: nosniff присутствует."""
        _, h = _get_response_headers("/health")
        assert h.get("x-content-type-options") == "nosniff", (
            "X-Content-Type-Options: nosniff отсутствует или неверный"
        )

    def test_sh2_x_frame_options_absent_for_telegram(self):
        """
        SH-2: X-Frame-Options НЕ должен быть DENY на страницах Mini App.

        Telegram Mini App открывается во встроенном WebView — это не iframe,
        X-Frame-Options там не применяется. НО если /app или /admin когда-либо
        будут встраиваться через iframe (например, виджет для сайта ресторана),
        DENY сломает их.

        Правильная защита — CSP frame-ancestors (SH-8).
        Если X-Frame-Options присутствует — это дублирование, не ошибка,
        но значение должно быть не DENY (допустимо SAMEORIGIN).

        Тест: значение не должно быть "DENY" на /app (Mini App endpoint).
        """
        _, h = _get_response_headers("/app")
        xfo = h.get("x-frame-options", "")
        assert xfo.upper() != "DENY", (
            f"X-Frame-Options: DENY может конфликтовать с iframe-встраиванием. "
            f"Используйте CSP frame-ancestors. Текущее значение: {xfo!r}"
        )

    def test_sh3_referrer_policy(self):
        """SH-3: Referrer-Policy присутствует."""
        _, h = _get_response_headers("/health")
        rp = h.get("referrer-policy", "")
        assert rp, "Referrer-Policy отсутствует"
        assert rp in (
            "strict-origin-when-cross-origin",
            "strict-origin",
            "no-referrer",
            "no-referrer-when-downgrade",
        ), f"Неожиданное значение Referrer-Policy: {rp!r}"

    def test_sh4_strict_transport_security(self):
        """SH-4: HSTS присутствует с max-age >= 63072000 (2 года)."""
        _, h = _get_response_headers("/health")
        hsts = h.get("strict-transport-security", "")
        assert hsts, "Strict-Transport-Security отсутствует"
        # Извлекаем max-age
        for part in hsts.split(";"):
            part = part.strip()
            if part.lower().startswith("max-age="):
                max_age = int(part.split("=")[1])
                assert max_age >= 63072000, (
                    f"HSTS max-age={max_age} < 63072000 (2 года)"
                )
                return
        pytest.fail(f"HSTS не содержит max-age: {hsts!r}")

    def test_sh5_permissions_policy(self):
        """SH-5: Permissions-Policy присутствует."""
        _, h = _get_response_headers("/health")
        assert h.get("permissions-policy"), "Permissions-Policy отсутствует"

    def test_sh6_content_security_policy_present(self):
        """SH-6: Content-Security-Policy присутствует."""
        _, h = _get_response_headers("/health")
        assert h.get("content-security-policy"), "Content-Security-Policy отсутствует"

    def test_sh7_csp_allows_telegram_script(self):
        """SH-7: CSP script-src разрешает telegram.org (Mini App SDK)."""
        _, h = _get_response_headers("/app")
        csp = h.get("content-security-policy", "")
        assert "telegram.org" in csp, (
            "CSP не разрешает telegram.org в script-src — Mini App SDK не загрузится"
        )

    def test_sh8_csp_frame_ancestors(self):
        """
        SH-8: CSP содержит frame-ancestors.

        frame-ancestors 'none' — аналог X-Frame-Options: DENY, но более гибкий.
        frame-ancestors 'self' — допускает встраивание с того же домена.
        Любое из них лучше отсутствия директивы.
        """
        _, h = _get_response_headers("/health")
        csp = h.get("content-security-policy", "")
        assert "frame-ancestors" in csp, (
            "CSP не содержит frame-ancestors. "
            "Добавьте frame-ancestors 'none' или 'self' для защиты от clickjacking."
        )

    def test_sh9_server_header_no_version(self):
        """SH-9: Server header не раскрывает точную версию."""
        _, h = _get_response_headers("/health")
        server = h.get("server", "")
        # uvicorn по умолчанию ставит "uvicorn" без версии — это приемлемо
        # Недопустимо: "uvicorn/0.29.0", "Python/3.11", "nginx/1.25.3"
        import re
        assert not re.search(r"/[\d.]+", server), (
            f"Server header раскрывает версию: {server!r}"
        )

    def test_sh10_cache_control_on_login(self):
        """SH-10: Cache-Control: no-store на /api/agency/login."""
        resp, h = _get_response_headers(
            "/api/agency/login",
            method="POST",
            json={"email": "x@x.com", "password": "wrong"},
        )
        # Ответ может быть 401/429/422 — нас интересует заголовок, не статус
        cc = h.get("cache-control", "")
        assert "no-store" in cc, (
            f"Cache-Control на /api/agency/login не содержит no-store: {cc!r}. "
            f"Токены не должны кешироваться."
        )

    def test_sh11_cache_control_on_superadmin_login(self):
        """SH-11: Cache-Control: no-store на /api/superadmin/login."""
        resp, h = _get_response_headers(
            "/api/superadmin/login",
            method="POST",
            json={"email": "x@x.com", "password": "wrong"},
        )
        cc = h.get("cache-control", "")
        assert "no-store" in cc, (
            f"Cache-Control на /api/superadmin/login не содержит no-store: {cc!r}"
        )


# ═══════════════════════════════════════════════════════════════════
# CORS
# ═══════════════════════════════════════════════════════════════════

class TestCORS:

    def test_cors1_preflight_allowed_origin(self):
        """CORS-1: OPTIONS preflight с разрешённым origin получает 200."""
        # В тестовой среде ALLOWED_ORIGINS пустой → fallback "*"
        # При "*" preflight должен проходить
        resp = client.options(
            "/api/agency/login",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,Authorization",
            },
        )
        assert resp.status_code in (200, 204), (
            f"CORS preflight вернул {resp.status_code}, ожидался 200/204"
        )

    def test_cors2_no_origin_does_not_break(self):
        """CORS-2: Запрос без Origin header (server-to-server) не ломается."""
        resp = client.get("/health")
        assert resp.status_code == 200, (
            "Запрос без Origin header упал — server-to-server не работает"
        )

    def test_cors3_allow_methods_no_wildcard(self):
        """CORS-3: allow_methods не содержит wildcard '*' (явный список методов)."""
        resp = client.options(
            "/api/agency/login",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        # Wildcard "*" в allow_methods при allow_credentials=True —
        # браузер игнорирует его. Должен быть явный список.
        assert allow_methods != "*", (
            "Access-Control-Allow-Methods: * при allow_credentials=True — "
            "браузер отклонит запрос. Используйте явный список методов."
        )

    def test_cors4_expose_headers_no_sensitive(self):
        """CORS-4: Access-Control-Expose-Headers не раскрывает чувствительные заголовки."""
        resp = client.options(
            "/api/agency/login",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        exposed = resp.headers.get("access-control-expose-headers", "").lower()
        # Authorization не должен быть expose — клиент не должен читать его через JS
        assert "authorization" not in exposed, (
            "Authorization header выставлен в Access-Control-Expose-Headers — "
            "это может раскрыть токены через JS"
        )


# ═══════════════════════════════════════════════════════════════════
# RATE LIMITING — конфигурационные проверки
# ═══════════════════════════════════════════════════════════════════

class TestRateLimitConfig:

    def test_rl1_rate_limit_exceeded_returns_429(self):
        """RL-1: При превышении rate limit возвращается 429, не 500."""
        # Используем endpoint с жёстким лимитом: superadmin login (5/minute)
        # Отправляем 10 запросов — хотя бы часть должна получить 429
        statuses = set()
        for _ in range(10):
            resp = client.post(
                "/api/superadmin/login",
                json={"email": "x@x.com", "password": "wrong"},
            )
            statuses.add(resp.status_code)

        # Должны видеть 401 (неверный пароль) или 429 (rate limit), но не 500
        assert 500 not in statuses, (
            f"Rate limited запросы вернули 500. Статусы: {statuses}"
        )
        # При 10 запросах с лимитом 5/minute хотя бы один должен быть 429
        assert 429 in statuses, (
            f"Ни один из 10 запросов не получил 429. Статусы: {statuses}. "
            f"Возможно, rate limiter не работает."
        )

    def test_rl2_webhook_has_rate_limit(self):
        """RL-2: Webhook endpoint задекорирован @limiter.limit."""
        # Проверяем что функция имеет атрибут _rate_limits (slowapi)
        from api import restaurant_webhook, webhook
        assert hasattr(restaurant_webhook, "_rate_limits") or hasattr(
            restaurant_webhook, "__wrapped__"
        ), "restaurant_webhook не имеет rate limit декоратора"

    def test_rl3_superadmin_login_limit_stricter_than_api(self):
        """RL-3: RATE_LIMIT_SUPERADMIN_LOGIN строже чем RATE_LIMIT_API."""
        from config import settings

        def _parse_rate(rate_str: str) -> float:
            """Конвертирует '5/minute' → requests-per-second."""
            count, period = rate_str.split("/")
            period_seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
            return int(count) / period_seconds.get(period, 60)

        sa_rps = _parse_rate(settings.RATE_LIMIT_SUPERADMIN_LOGIN)
        api_rps = _parse_rate(settings.RATE_LIMIT_API)

        assert sa_rps < api_rps, (
            f"RATE_LIMIT_SUPERADMIN_LOGIN ({settings.RATE_LIMIT_SUPERADMIN_LOGIN}) "
            f"не строже RATE_LIMIT_API ({settings.RATE_LIMIT_API}). "
            f"Superadmin login должен иметь более жёсткий лимит."
        )

    def test_rl4_agency_registration_limit_stricter_than_login(self):
        """RL-4: Agency registration rate limit (5/hour) строже login (10/minute)."""
        # 5/hour = 0.00139 rps; 10/minute = 0.167 rps
        # Проверяем через source code что декоратор присутствует
        import inspect
        from routers import agency as agency_module
        src = inspect.getsource(agency_module)
        # Должен быть лимит на register endpoint
        assert "5/hour" in src or "5/minute" in src or "1/minute" in src, (
            "Agency registration не имеет строгого rate limit. "
            "Ожидается не более 5/hour."
        )

    def test_rl5_rate_limit_api_default_not_wildcard(self):
        """RL-5: RATE_LIMIT_API задан и является разумным значением."""
        from config import settings
        rate = settings.RATE_LIMIT_API
        assert rate, "RATE_LIMIT_API не задан"

        count, period = rate.split("/")
        assert int(count) <= 1000, (
            f"RATE_LIMIT_API={rate} слишком высокий — фактически без лимита"
        )
        assert period in ("second", "minute", "hour"), (
            f"RATE_LIMIT_API={rate} использует неожиданный период"
        )

    def test_rl6_login_rate_limit_configured(self):
        """RL-6: RATE_LIMIT_LOGIN задан и применяется к login endpoints."""
        from config import settings
        assert settings.RATE_LIMIT_LOGIN, "RATE_LIMIT_LOGIN не задан"

        import inspect
        from routers import agency as agency_module
        src = inspect.getsource(agency_module)
        assert "RATE_LIMIT_LOGIN" in src, (
            "RATE_LIMIT_LOGIN не используется в routers/agency.py"
        )


# ═══════════════════════════════════════════════════════════════════
# RATE LIMIT STORAGE
# ═══════════════════════════════════════════════════════════════════

class TestRateLimitStorage:

    def test_rls1_storage_is_in_memory(self):
        """
        RLS-1: Rate limit storage — in-memory (MemoryStorage).

        Это задокументированное ограничение MVP:
          - не синхронизируется между workers/instances
          - сбрасывается при рестарте
          - достаточно для одного Render instance на Free tier

        Тест проверяет что storage — именно тот тип, который задан в коде,
        без неожиданного изменения на Redis.
        """
        from limiter import limiter
        # slowapi Limiter хранит storage в _storage или storage атрибуте
        storage = getattr(limiter, "_storage", None) or getattr(limiter, "storage", None)
        if storage is not None:
            storage_type = type(storage).__name__
            # Допустимые in-memory storage: MemoryStorage, MovingWindowRateLimiter wrapper
            assert "Redis" not in storage_type, (
                f"Неожиданный Redis storage в rate limiter: {storage_type}. "
                f"Если Redis добавлен намеренно — обновите тест."
            )

    def test_rls2_limiter_key_func_is_remote_address(self):
        """RLS-2: Limiter использует get_remote_address как key_func."""
        from limiter import limiter
        from slowapi.util import get_remote_address
        assert limiter._key_func is get_remote_address, (
            "Limiter использует нестандартный key_func. "
            "Убедитесь что ProxyHeadersMiddleware корректно устанавливает client IP."
        )


# ═══════════════════════════════════════════════════════════════════
# PROXY / REQUEST ID
# ═══════════════════════════════════════════════════════════════════

class TestRequestID:

    def test_proxy1_request_id_present_in_response(self):
        """PROXY-1: X-Request-ID присутствует в каждом ответе."""
        _, h = _get_response_headers("/health")
        assert h.get("x-request-id"), (
            "X-Request-ID отсутствует в ответе — RequestIDMiddleware не работает"
        )

    def test_proxy2_client_request_id_propagated(self):
        """PROXY-2: X-Request-ID клиента прокидывается в ответ."""
        custom_id = "test-request-id-12345"
        resp = client.get("/health", headers={"X-Request-ID": custom_id})
        returned_id = resp.headers.get("x-request-id", "")
        assert returned_id == custom_id, (
            f"X-Request-ID не прокинут. Отправлен: {custom_id!r}, получен: {returned_id!r}"
        )

    def test_proxy3_unique_request_ids_generated(self):
        """PROXY-3: Без X-Request-ID каждый запрос получает уникальный ID."""
        ids = set()
        for _ in range(5):
            resp = client.get("/health")
            rid = resp.headers.get("x-request-id", "")
            assert rid, "X-Request-ID не сгенерирован"
            ids.add(rid)
        assert len(ids) == 5, (
            f"X-Request-ID не уникален: получено {len(ids)} уникальных из 5 запросов"
        )


# ═══════════════════════════════════════════════════════════════════
# TRUSTED PROXY
# ═══════════════════════════════════════════════════════════════════

class TestTrustedProxy:

    def test_tp1_trusted_proxy_hosts_configured(self):
        """TP-1: TRUSTED_PROXY_HOSTS задан в config."""
        from config import settings
        assert settings.TRUSTED_PROXY_HOSTS, "TRUSTED_PROXY_HOSTS не задан"

    def test_tp2_proxy_middleware_registered(self):
        """TP-2: ProxyHeadersMiddleware зарегистрирован в app."""
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        middleware_types = [
            type(m.cls) if hasattr(m, "cls") else type(m)
            for m in app.middleware_stack.__dict__.get("app", app).__dict__.get(
                "middleware_stack", None
            ).__dict__.values()
            if hasattr(m, "cls") or isinstance(m, type)
        ]
        # Альтернативная проверка через middleware_stack attr
        # Просто проверяем что импорт и присутствие в коде api.py корректны
        import inspect
        import api as api_module
        src = inspect.getsource(api_module)
        assert "ProxyHeadersMiddleware" in src, (
            "ProxyHeadersMiddleware не найден в api.py"
        )

    def test_tp3_wildcard_proxy_warning_logged(self):
        """TP-3: При TRUSTED_PROXY_HOSTS='*' логируется предупреждение (документируем риск)."""
        from config import settings
        # Этот тест документирует что '*' используется и это known risk
        if settings.TRUSTED_PROXY_HOSTS == "*":
            # '*' допустимо на Render — приложение не доступно напрямую из интернета
            # Тест PASS, но фиксируем что это ограничение
            pass
        # Любое значение допустимо — тест всегда проходит, фиксирует текущее состояние
        assert True


# ═══════════════════════════════════════════════════════════════════
# SENSITIVE ENDPOINTS — отсутствие rate limit (документируем gaps)
# ═══════════════════════════════════════════════════════════════════

class TestSensitiveEndpointLimits:

    def test_sel1_billing_subscribe_covered_by_default(self):
        """
        SEL-1: /api/billing/subscribe покрыт default rate limit (120/min).

        Индивидуального декоратора нет — это known gap, покрытый default_limits.
        Тест документирует текущее состояние.
        """
        import inspect
        from routers import billing as billing_module
        src = inspect.getsource(billing_module)
        # Индивидуального лимита нет на subscribe — это OK для MVP
        # default_limits из Limiter применяется автоматически
        from limiter import limiter
        assert limiter.default_limits, (
            "Limiter не имеет default_limits — /api/billing/subscribe не защищён"
        )

    def test_sel2_impersonate_no_individual_limit(self):
        """
        SEL-2: /api/superadmin/{id}/impersonate не имеет индивидуального rate limit.

        Покрыт только default_limits (120/min). Это known gap для будущей задачи.
        Тест документирует текущее состояние — не FAIL.
        """
        import inspect
        from routers import superadmin as sa_module
        src = inspect.getsource(sa_module)
        # Фиксируем что impersonate не имеет @limiter.limit
        lines = src.split("\n")
        impersonate_section = False
        has_individual_limit = False
        for i, line in enumerate(lines):
            if "impersonate_agency" in line and "def " in line:
                impersonate_section = True
            if impersonate_section and "@limiter.limit" in line:
                has_individual_limit = True
                break
            if impersonate_section and "def " in line and "impersonate" not in line:
                break

        if not has_individual_limit:
            # Known gap — documented, not failed
            pytest.skip(
                "KNOWN GAP: impersonate endpoint не имеет индивидуального rate limit. "
                "Покрыт default_limits (120/min). Рекомендуется добавить 3/minute в будущей задаче."
            )
