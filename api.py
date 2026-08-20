"""
api.py — Taomly Platform
Точка входа FastAPI приложения.

Изменения v10 (Foundation Task 11 — Production Hardening):
  - lifespan: вызывает purge_expired_revoked_tokens() при старте.
    Удаляет истёкшие JWT-revocation записи накопившиеся между рестартами.
    Ошибка purge не прерывает запуск приложения — логируется и игнорируется.
    Probabilistic purge при logout не нужен: ACCESS_TOKEN_EXPIRE_HOURS=8,
    Render перезапускается при каждом deploy — startup достаточен.

Изменения v3:
  - config.py: все env-переменные читаются из единого модуля
  - CORSMiddleware: настроен с ALLOWED_ORIGINS из config
  - SecurityHeadersMiddleware: CSP, X-Frame-Options, X-Content-Type-Options,
    Referrer-Policy, Permissions-Policy
  - slowapi Rate Limiting: /api/agency/login и /api/agency/restaurant-login
    ограничены 10 req/min; публичные API — 120 req/min
  - Sentry: инициализируется при наличии SENTRY_DSN в env
  - robots.txt и favicon отдаются из static/

Изменения v4 (Stage 2 Sprint 1):
  - GET /manifest/{slug}.json — динамический PWA manifest per-restaurant

Изменения v5 (Stage 2 Sprint 3):
  - AI роутер подключён (заглушки, AI_ENABLED=false по умолчанию)

Изменения v6 (Security Patch):
  - GET /sw.js — версионированный Service Worker с инжектированным BUILD_HASH
  - CORS warning при allow_origins=["*"]
  - Response импортирован явно

Изменения v7 (Security Patch SEC-4):
  - ProxyHeadersMiddleware: rate limiting теперь работает корректно за Render proxy

Изменения v8 (Foundation Task 7 — Security Headers):
  - SecurityHeadersMiddleware: X-Frame-Options: DENY заменён на CSP frame-ancestors 'none'
    (CSP Level 2+ имеет приоритет; убирает риск конфликта при iframe-виджетах ресторанов)
  - SecurityHeadersMiddleware: Cache-Control: no-store добавлен на auth endpoints
    (login, restaurant-login, superadmin-login, register)
  - CSP: добавлена директива frame-ancestors 'none'

Изменения v9 (Foundation Task 9 — API Versioning):
  - ApiVersioningMiddleware: path-rewriting /api/v1/* → /api/* на уровне ASGI scope.
    Роутеры регистрируются один раз — нет operation_id конфликтов, нет дублирования.
    Rate limiting (slowapi) видит переписанный путь — один bucket для /api/v1 и /api.
    SecurityHeadersMiddleware._NO_CACHE_PATHS работает автоматически (path уже переписан).
    /webhook, /health, /, /sw.js — не затронуты.
    Существующие /api/* endpoints продолжают работать без изменений.
"""

import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date

import sentry_sdk
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import handlers
import models
import telebot
from config import settings
from database import SessionLocal, engine
from routers import agency, analytics, billing, menu, orders, reservations, restaurants, waiter_calls, ai, superadmin

# ──────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# SECRET REDACTION (Foundation Task 5)
# Ключи, значения которых никогда не должны попасть в Sentry event
# (extra/context/tags), даже если где-то в коде их случайно передадут.
# Это defense-in-depth поверх send_default_pii=False — основная защита
# в том, что мы просто нигде не кладём эти значения в Sentry-контекст.
# ──────────────────────────────────────────
_SENTRY_REDACT_KEYS = {
    "password", "password_hash", "authorization", "bearer",
    "access_token", "refresh_token", "secret_key", "fernet_key",
    "superadmin_password_hash", "webhook_secret", "telegram_bot_token",
    "telegram_bot_token_encrypted", "x-telegram-init-data", "init_data",
    "x-telegram-bot-api-secret-token",
}


def _scrub_sentry_event(event: dict, hint: dict) -> dict:
    """
    before_send: последний рубеж защиты от утечки секретов в Sentry.

    Рекурсивно вычищает известные секретные ключи из request/extra/contexts.
    Не полагается на это как на единственную защиту — основная защита в том,
    что секреты просто нигде не передаются в capture_exception()/set_context().
    """
    def _scrub(obj):
        if isinstance(obj, dict):
            return {
                k: ("[REDACTED]" if k.lower() in _SENTRY_REDACT_KEYS else _scrub(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_scrub(v) for v in obj]
        return obj

    for key in ("request", "extra", "contexts"):
        if key in event:
            event[key] = _scrub(event[key])
    return event


# ──────────────────────────────────────────
# SENTRY (опционально — включается при наличии SENTRY_DSN)
# ──────────────────────────────────────────
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=settings.ENVIRONMENT,
        # send_default_pii=False (default): не отправляем headers/cookies/IP
        # автоматически — минимизация PII, как решено для Taomly.
        send_default_pii=False,
        # Значения локальных переменных в stack frame НЕ отправляем: функции
        # вроде decrypt_token()/verify_telegram_init_data() держат bot_token
        # и производные секретных ключей в локальных переменных — если бы
        # там возникло исключение, Sentry по умолчанию прикрепил бы их
        # значения к событию. Отключаем этот вектор утечки целиком.
        include_local_variables=False,
        before_send=_scrub_sentry_event,
    )
    logger.info("Sentry инициализирован (environment=%s)", settings.ENVIRONMENT)
else:
    logger.warning("SENTRY_DSN не задан — мониторинг ошибок отключён")

# ──────────────────────────────────────────
# RATE LIMITER
# ──────────────────────────────────────────
from limiter import limiter


# ──────────────────────────────────────────
# SECURITY HEADERS MIDDLEWARE
# ──────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    # Пути, чьи ответы содержат токены — кешировать нельзя.
    _NO_CACHE_PATHS = frozenset([
        "/api/agency/login",
        "/api/agency/restaurant-login",
        "/api/superadmin/login",
        "/api/agency/register",
    ])

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # X-Content-Type-Options: защита от MIME-sniffing атак
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Referrer-Policy: не утекаем полный URL при переходах на внешние сайты
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # HSTS: только HTTPS, 2 года. Не ставим preload — это необратимо.
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

        # Permissions-Policy: запрещаем ненужные браузерные API
        response.headers["Permissions-Policy"] = (
            "geolocation=(self), camera=(), microphone=(), payment=()"
        )

        # Cache-Control: no-store на auth endpoints.
        # JWT-ответы не должны кешироваться proxy/браузером.
        # Не перезаписываем если роутер уже задал Cache-Control (например /sw.js).
        if request.url.path in self._NO_CACHE_PATHS and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"

        # CSP: не перезаписываем если роутер уже установил свой.
        #
        # X-Frame-Options: DENY — НАМЕРЕННО УДАЛЁН.
        # Причина: CSP frame-ancestors является современной заменой и имеет приоритет
        # над X-Frame-Options в браузерах с поддержкой CSP Level 2+. Дублирование
        # создаёт риск конфликта при будущем добавлении iframe-виджетов для ресторанов.
        # frame-ancestors 'none' эквивалентен DENY и принимается всеми целевыми браузерами.
        # Telegram Mini App работает через встроенный WebView — не iframe — X-Frame-Options
        # там не применяется в любом случае.
        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' "
                "  https://cdn.jsdelivr.net "
                "  https://cdnjs.cloudflare.com "
                "  https://telegram.org; "
                "style-src 'self' 'unsafe-inline' "
                "  https://fonts.googleapis.com "
                "  https://cdnjs.cloudflare.com "
                "  https://cdn.jsdelivr.net; "
                "font-src 'self' "
                "  https://fonts.gstatic.com "
                "  https://cdnjs.cloudflare.com; "
                "img-src 'self' data: https: blob:; "
                "connect-src 'self' "
                "  https://api.telegram.org "
                "  https://*.r2.dev; "
                "worker-src 'self'; "
                "manifest-src 'self'; "
                "frame-ancestors 'none';"
            )
        return response


# ──────────────────────────────────────────
# REQUEST ID MIDDLEWARE (Foundation Task 5, п.12)
# Простой UUID per request — не distributed tracing, только:
#   входящий X-Request-ID (если клиент/прокси уже его проставил) уважается,
#   иначе генерируется новый; кладётся в request.state, в response header
#   и используется как Sentry tag / часть лога в глобальном exception handler.
# ──────────────────────────────────────────
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ──────────────────────────────────────────
# API VERSIONING MIDDLEWARE (Foundation Task 9)
#
# Стратегия: path-rewriting на уровне ASGI scope.
#
# /api/v1/{rest}  →  /api/{rest}  (до FastAPI routing)
#
# Почему не двойной include_router():
#   - 6 из 10 роутеров имеют prefix="/api/XXX" внутри самого роутера.
#     include_router(router, prefix="/api/v1") даёт /api/v1/api/XXX — неправильно.
#   - Двойная регистрация создаёт дублирующиеся operation_id в OpenAPI схеме
#     (даже при отключённой docs_url).
#
# Почему не HTTP 307 redirect:
#   - Лишний roundtrip для каждого запроса клиента.
#   - CORS preflight может не следовать за редиректом в некоторых браузерах.
#
# Почему ASGI middleware (не BaseHTTPMiddleware):
#   - BaseHTTPMiddleware буферизует тело запроса дважды (известный overhead).
#   - Чистый ASGI middleware переписывает scope['path'] до любой обработки:
#     SecurityHeadersMiddleware, rate limiting, роутер — все видят /api/... путь.
#
# Побочные эффекты:
#   - slowapi rate limit bucket: /api/v1/agency/login и /api/agency/login
#     используют ОДИН bucket (/api/agency/login). Логически правильно —
#     это один и тот же endpoint.
#   - _NO_CACHE_PATHS в SecurityHeadersMiddleware: path уже переписан →
#     no-store применяется к /api/agency/login корректно.
#   - sw.js: startsWith('/api/') перехватывает /api/v1/* ПОСЛЕ rewriting →
#     Network First стратегия применяется корректно.
# ──────────────────────────────────────────
_V1_PREFIX = "/api/v1"
_V1_PREFIX_LEN = len(_V1_PREFIX)
_API_PREFIX = "/api"


class ApiVersioningMiddleware:
    """
    ASGI middleware: /api/v1/{path} → /api/{path}.

    Переписывает scope['path'] и scope['raw_path'] до FastAPI dispatch.
    Не буферизует тело запроса. Не меняет response.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path: str = scope.get("path", "")
            if path.startswith(_V1_PREFIX + "/") or path == _V1_PREFIX:
                # /api/v1/agency/login  →  /api/agency/login
                new_path = _API_PREFIX + path[_V1_PREFIX_LEN:]
                scope = dict(scope)  # копируем — не мутируем оригинал
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode("latin-1")
        await self.app(scope, receive, send)


# ──────────────────────────────────────────
# LIFESPAN
# ──────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Lifespan: запуск приложения. Схема управляется Alembic.")

    # Foundation Task 11.1: очистка истёкших revoked_tokens при старте.
    # Токены живут ACCESS_TOKEN_EXPIRE_HOURS=8 часов; при каждом рестарте
    # (deploy или Render cold-start) накопленные истёкшие записи удаляются.
    # Это предотвращает рост таблицы и деградацию decode_token() SELECT.
    # Ошибка не прерывает запуск — безопасно: истёкшие токены и без того
    # недействительны по полю exp в самом JWT.
    try:
        from auth import purge_expired_revoked_tokens
        _db = SessionLocal()
        try:
            deleted = purge_expired_revoked_tokens(_db)
            if deleted:
                logger.info("Startup: удалено %d истёкших revoked_tokens", deleted)
        finally:
            _db.close()
    except Exception:
        logger.exception("Startup: не удалось выполнить purge revoked_tokens — продолжаем")

    if handlers.platform_bot:
        try:
            handlers.platform_bot.remove_webhook()
            if settings.WEBHOOK_URL:
                handlers.platform_bot.set_webhook(
                    url=f"{settings.WEBHOOK_URL}/webhook",
                    secret_token=settings.WEBHOOK_SECRET,
                )
                logger.info("Webhook установлен: %s/webhook", settings.WEBHOOK_URL)
            else:
                logger.warning("WEBHOOK_URL не задан — webhook не установлен")
        except Exception as exc:
            logger.exception("Не удалось установить webhook — приложение продолжает работу")
            sentry_sdk.capture_exception(exc)
    else:
        logger.warning("BOT_TOKEN не задан — платформенный бот отключён")

    yield

    if handlers.platform_bot:
        try:
            handlers.platform_bot.remove_webhook()
            logger.info("Webhook снят при остановке")
        except Exception:
            logger.exception("Ошибка при снятии webhook")


# ──────────────────────────────────────────
# APP
# ──────────────────────────────────────────
app = FastAPI(
    title="Taomly White Label Platform",
    description="Multi-tenant restaurant SaaS engine",
    version="2.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# ──────────────────────────────────────────
# RATE LIMITER
# ──────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ──────────────────────────────────────────
# CORS
# ──────────────────────────────────────────
if settings.ALLOWED_ORIGINS:
    _cors_origins = settings.ALLOWED_ORIGINS
else:
    if settings.ENVIRONMENT == "production":
        raise RuntimeError(
            "[STARTUP ERROR] ENVIRONMENT=production, но ALLOWED_ORIGINS не задан. "
            "Задайте в Render Dashboard → Environment Variables: "
            "ALLOWED_ORIGINS=https://your-app-domain.com "
            "(плюс остальные ваши домены через запятую)."
        )
    import sys
    print(
        "\n[CORS WARNING] ALLOWED_ORIGINS не задан — CORS разрешает ВСЕ origins (*).\n"
        "В production задайте: ALLOWED_ORIGINS=https://your-app-domain.com\n",
        file=sys.stderr,
    )
    _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Telegram-Init-Data",
        "X-Restaurant-Id",
    ],
)

# ──────────────────────────────────────────
# SECURITY HEADERS
# ──────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)

# ──────────────────────────────────────────
# REQUEST ID
# BaseHTTPMiddleware стек выполняется в порядке, обратном добавлению —
# регистрируем сразу после security headers, чтобы request_id был
# доступен максимально рано (до роутеров и до глобального handler'а).
# ──────────────────────────────────────────
app.add_middleware(RequestIDMiddleware)


# ──────────────────────────────────────────
# GLOBAL EXCEPTION HANDLER (Foundation Task 5, п.13)
#
# До этой задачи необработанное исключение долетало до дефолтного
# Starlette ServerErrorMiddleware: клиенту уходил безопасный ответ
# (текст "Internal Server Error", без traceback — т.к. FastAPI(debug=...)
# нигде не включён), НО:
#   - в формате plain text, а не в JSON-контракте остального API;
#   - без request_id;
#   - Sentry получал событие автоматически (ASGI-интеграция), но без
#     единообразного тега request_id, который был бы в логах.
#
# Этот handler не меняет поведение для HTTPException/RequestValidationError
# (они остаются в стандартной FastAPI-обработке — 401/403/404/422/409/429
# и т.д. НЕ проходят через него и НЕ уходят в Sentry, см. п.23) —
# он ловит только по-настоящему неожиданные исключения.
# ──────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())

    # exc_info=exc (не просто exception()) — не полагаемся на то, что
    # sys.exc_info() всё ещё активен к моменту вызова хендлера;
    # передаём объект исключения явно, чтобы traceback гарантированно
    # попал в лог независимо от того, как Starlette вызвала хендлер.
    logger.error(
        "Необработанное исключение: method=%s path=%s request_id=%s",
        request.method, request.url.path, request_id,
        exc_info=exc,
    )

    # Sentry's ASGI-интеграция уже изолирует scope на запрос — просто
    # проставляем тег в текущий (per-request) scope перед капчуром.
    sentry_sdk.set_tag("request_id", request_id)
    sentry_sdk.capture_exception(exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "request_id": request_id},
    )

# ──────────────────────────────────────────
# PROXY HEADERS (SEC-4)
# Render и любой reverse proxy передают реальный IP клиента
# в заголовке X-Forwarded-For. Без этого middleware slowapi
# видит IP прокси и rate limit применяется ко всем сразу.
#
# TRUSTED_PROXY_HOSTS задаётся через env:
#   "*"         — доверять любому proxy (Render, Railway, аналоги)
#   "127.0.0.1" — только локальный proxy (собственный сервер с nginx)
#   "10.0.0.1"  — конкретный IP load balancer
#
# Не используйте "*" если приложение доступно напрямую из интернета
# без proxy — это позволит подделать X-Forwarded-For.
# ──────────────────────────────────────────
_trusted_proxy_hosts = settings.TRUSTED_PROXY_HOSTS
if _trusted_proxy_hosts == "*":
    logger.warning(
        "TRUSTED_PROXY_HOSTS='*' — X-Forwarded-For принимается от любого хоста. "
        "Допустимо на Render/Railway. Для собственного сервера задайте конкретный IP."
    )
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_proxy_hosts)

# ──────────────────────────────────────────
# API VERSIONING (Foundation Task 9)
# Регистрируется ПОСЛЕДНИМ — в стеке Starlette middleware выполняется ПЕРВЫМ.
# Переписывает /api/v1/* → /api/* до любого другого middleware и роутера.
# ──────────────────────────────────────────
app.add_middleware(ApiVersioningMiddleware)

# ──────────────────────────────────────────
# ROUTERS
# ──────────────────────────────────────────
app.include_router(menu.router,         prefix="/api/menu",         tags=["menu"])
app.include_router(orders.router,       prefix="/api/orders",       tags=["orders"])
app.include_router(reservations.router, prefix="/api/reservations", tags=["reservations"])
app.include_router(waiter_calls.router, prefix="/api/waiter-calls", tags=["waiter-calls"])
app.include_router(analytics.router)
app.include_router(billing.router)
app.include_router(restaurants.router)
app.include_router(agency.router)
app.include_router(ai.router)
app.include_router(superadmin.router)

# ──────────────────────────────────────────
# STATIC
# ──────────────────────────────────────────
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
else:
    logger.warning("Папка static/ не найдена")

# i18n JSON файлы — доступны фронтенду по /i18n/{lang}.json
if os.path.exists("i18n"):
    app.mount("/i18n", StaticFiles(directory="i18n"), name="i18n")
else:
    logger.warning("Папка i18n/ не найдена — переводы недоступны через HTTP")


# ──────────────────────────────────────────
# СЛУЖЕБНЫЕ ЭНДПОИНТЫ
# ──────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "running", "app": "Taomly", "version": "2.1.0"}


@app.get("/health")
def health():
    try:
        import sqlalchemy
        with SessionLocal() as db:
            db.execute(sqlalchemy.text("SELECT 1"))
        return {"status": "healthy", "db": "ok"}
    except Exception:
        logger.exception("Health check: БД недоступна")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "db": "error"},
        )


@app.get("/app")
def serve_app():
    return FileResponse("static/index.html")


@app.get("/admin")
def serve_admin():
    return FileResponse("static/admin.html")


@app.get("/agency-admin")
def serve_agency_admin():
    return FileResponse("static/agency_admin.html")


@app.get("/superadmin")
def serve_superadmin():
    return FileResponse("static/superadmin.html")


@app.get("/robots.txt")
def serve_robots():
    return FileResponse("static/robots.txt", media_type="text/plain")


@app.get("/favicon.ico")
def serve_favicon():
    if os.path.exists("static/favicon.ico"):
        return FileResponse("static/favicon.ico")
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


@app.get("/sw.js")
def serve_sw(response: Response):
    """
    Service Worker с инжектированной версией кэша.

    Версия = первые 8 символов BUILD_HASH env (задаётся при деплое на Render).
    Fallback: текущая дата YYYYMMDD — гарантирует сброс кэша раз в сутки.

    Браузер получает SW без кэша (no-cache), чтобы сразу видеть обновления.
    """
    build_hash = os.getenv("BUILD_HASH", "")
    if build_hash:
        version = build_hash[:8]
    else:
        version = date.today().strftime("%Y%m%d")

    with open("static/sw.js", "r", encoding="utf-8") as f:
        content = f.read()

    content = content.replace("'taomly-dev'", f"'{version}'")

    return Response(
        content=content,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


# ──────────────────────────────────────────
# DYNAMIC PWA MANIFEST (White Label)
# ──────────────────────────────────────────
@app.get("/manifest/{slug}.json")
def dynamic_manifest(slug: str):
    with SessionLocal() as db:
        restaurant = db.query(models.Restaurant).filter(
            models.Restaurant.slug == slug.lower().strip(),
            models.Restaurant.is_active == True,
        ).first()

    if not restaurant:
        return JSONResponse(content={
            "name": "Taomly",
            "short_name": "Taomly",
            "description": "Заказ еды через Telegram Mini App",
            "start_url": "/app",
            "display": "standalone",
            "background_color": "#FAF6EE",
            "theme_color": "#8B1A2E",
            "orientation": "portrait-primary",
            "lang": "uz",
            "scope": "/",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192",
                 "type": "image/png", "purpose": "any"},
                {"src": "/static/icon-512.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "any maskable"},
                {"src": "/static/apple-touch-icon.png", "sizes": "180x180",
                 "type": "image/png", "purpose": "any"},
            ],
            "categories": ["food", "lifestyle"],
        }, media_type="application/manifest+json")

    return JSONResponse(content={
        "name": restaurant.name,
        "short_name": restaurant.name[:12],
        "description": restaurant.description or f"Заказ еды в {restaurant.name}",
        "start_url": f"/app?slug={restaurant.slug}",
        "display": "standalone",
        "background_color": restaurant.secondary_color or "#FAF6EE",
        "theme_color": restaurant.primary_color or "#8B1A2E",
        "orientation": "portrait-primary",
        "lang": "uz",
        "scope": "/",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192",
             "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/apple-touch-icon.png", "sizes": "180x180",
             "type": "image/png", "purpose": "any"},
        ],
        "categories": ["food", "lifestyle"],
    }, media_type="application/manifest+json")


# ──────────────────────────────────────────
# WEBHOOK — ресторанный бот (Multi-Tenant)
# ──────────────────────────────────────────
@app.post("/webhook/{slug}")
@limiter.limit("300/minute")
def restaurant_webhook(
    request: Request,
    slug: str,
    update: dict,
    x_telegram_bot_api_secret_token: str = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
):
    if not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "",
        settings.WEBHOOK_SECRET,
    ):
        logger.warning(
            "Webhook[%s]: отклонён запрос с невалидным секретом от %s",
            slug, request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    with SessionLocal() as db:
        restaurant = db.query(models.Restaurant).filter(
            models.Restaurant.slug == slug,
            models.Restaurant.is_active == True,
        ).first()

        if not restaurant or not restaurant.telegram_bot_token_encrypted:
            logger.warning("Webhook[%s]: ресторан не найден или бот не настроен", slug)
            return {"ok": False, "detail": "Ресторан не найден"}

        try:
            handlers.process_restaurant_webhook_update(restaurant, update)
            return {"ok": True}
        except Exception as exc:
            # Отвечаем 200/{"ok": False} — это намеренно (не HTTPException):
            # Telegram ретраит webhook при не-2xx ответе, а битый update
            # ретраить бессмысленно. Ошибка при этом не должна быть "немой" —
            # локально логируем и репортим в Sentry, иначе поломки обработки
            # апдейтов конкретного ресторана никогда не станут видны.
            logger.exception("Webhook[%s]: ошибка обработки update", slug)
            sentry_sdk.set_tag("restaurant_slug", slug)
            sentry_sdk.capture_exception(exc)
            return {"ok": False}


# ──────────────────────────────────────────
# WEBHOOK — платформенный бот
# ──────────────────────────────────────────
@app.post("/webhook")
@limiter.limit("300/minute")
def webhook(
    request: Request,
    update: dict,
    x_telegram_bot_api_secret_token: str = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
):
    if not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "",
        settings.WEBHOOK_SECRET,
    ):
        logger.warning(
            "Webhook: отклонён запрос с невалидным секретом от %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if not handlers.platform_bot:
        return {"ok": False, "detail": "Платформенный бот не настроен"}

    try:
        update_obj = telebot.types.Update.de_json(update)
        handlers.platform_bot.process_new_updates([update_obj])
        return {"ok": True}
    except Exception as exc:
        logger.exception("Ошибка обработки webhook update")
        sentry_sdk.capture_exception(exc)
        return {"ok": False}
