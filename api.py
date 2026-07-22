"""
api.py — Taomly Platform
Точка входа FastAPI приложения.

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
"""

import hmac
import logging
import os
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
# SENTRY (опционально — включается при наличии SENTRY_DSN)
# ──────────────────────────────────────────
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment="production",
    )
    logger.info("Sentry инициализирован")
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
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(self), camera=(), microphone=(), payment=()"
        )
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
            "connect-src 'self' https://api.telegram.org; "
            "worker-src 'self'; "
            "manifest-src 'self';"
        )
        return response


# ──────────────────────────────────────────
# LIFESPAN
# ──────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Lifespan: запуск приложения. Схема управляется Alembic.")

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
        except Exception:
            logger.exception("Не удалось установить webhook — приложение продолжает работу")
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
_cors_origins = settings.ALLOWED_ORIGINS if settings.ALLOWED_ORIGINS else ["*"]
if _cors_origins == ["*"]:
    import sys
    print(
        "\n[TAOMLY CORS WARNING] ALLOWED_ORIGINS не задан — CORS разрешает ВСЕ origins (*).\n"
        "В production обязательно задайте: ALLOWED_ORIGINS=https://taomly.uz,https://yourdomain.com\n",
        file=sys.stderr,
    )

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
# PROXY HEADERS (SEC-4)
# Render и любой reverse proxy передают реальный IP клиента
# в заголовке X-Forwarded-For. Без этого middleware slowapi
# видит IP прокси и rate limit применяется ко всем сразу.
# trusted_hosts="*" безопасно: X-Forwarded-Host не используется.
# ──────────────────────────────────────────
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

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
        except Exception:
            logger.exception("Webhook[%s]: ошибка обработки update", slug)
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
    except Exception:
        logger.exception("Ошибка обработки webhook update")
        return {"ok": False}
