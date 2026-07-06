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
"""

import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

import handlers
import models
import telebot
from config import settings
from database import SessionLocal, engine
from routers import agency, menu, orders, reservations, restaurants, waiter_calls

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
        # Трассировка 10% запросов — баланс между видимостью и стоимостью
        traces_sample_rate=0.1,
        # Профилировщик для медленных запросов
        profiles_sample_rate=0.1,
        environment="production",
    )
    logger.info("Sentry инициализирован")
else:
    logger.warning("SENTRY_DSN не задан — мониторинг ошибок отключён")

# ──────────────────────────────────────────
# RATE LIMITER
# ──────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


# ──────────────────────────────────────────
# SECURITY HEADERS MIDDLEWARE
# ──────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Добавляет security headers ко всем ответам.

    CSP: разрешаем только домены, реально используемые в проекте.
    Не используем 'unsafe-eval' — повышает защиту от XSS.
    'unsafe-inline' для scripts и styles временно — до рефакторинга
    inline-скриптов в HTML файлах на Stage 2.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Запрещаем встраивание в iframe — защита от clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Запрещаем браузеру угадывать MIME-тип — защита от MIME sniffing attacks
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Передаём только origin при cross-origin запросах (без full URL)
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Ограничиваем доступ к browser features
        response.headers["Permissions-Policy"] = (
            "geolocation=(self), camera=(), microphone=(), payment=()"
        )

        # Content Security Policy
        # TODO Stage 2: вынести inline JS/CSS во внешние файлы и убрать 'unsafe-inline'
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
    # MVP: create_all создаёт таблицы если не существуют.
    # TODO Stage 2: заменить на Alembic-миграции.
    models.Base.metadata.create_all(bind=engine)
    logger.info("БД инициализирована")

    # Устанавливаем webhook для платформенного бота
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

    # Shutdown
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
    # Отключаем /docs и /redoc в продакшене — не раскрываем API структуру
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# ──────────────────────────────────────────
# RATE LIMITER — должен быть добавлен ДО middleware
# ──────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ──────────────────────────────────────────
# CORS
# ──────────────────────────────────────────
# ALLOWED_ORIGINS из env: "https://taomly.uz,https://taomly.onrender.com"
# Если не задан — в development mode разрешаем всё (["*"])
_cors_origins = settings.ALLOWED_ORIGINS if settings.ALLOWED_ORIGINS else ["*"]

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
# ROUTERS
# ──────────────────────────────────────────
app.include_router(menu.router,         prefix="/api/menu",         tags=["menu"])
app.include_router(orders.router,       prefix="/api/orders",       tags=["orders"])
app.include_router(reservations.router, prefix="/api/reservations", tags=["reservations"])
app.include_router(waiter_calls.router, prefix="/api/waiter-calls", tags=["waiter-calls"])
app.include_router(restaurants.router)
app.include_router(agency.router)

# ──────────────────────────────────────────
# STATIC
# ──────────────────────────────────────────
import os
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
    """
    Health check для Render.
    Возвращает 503 если БД недоступна.
    """
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


@app.get("/robots.txt")
def serve_robots():
    return FileResponse("static/robots.txt", media_type="text/plain")


@app.get("/favicon.ico")
def serve_favicon():
    if os.path.exists("static/favicon.ico"):
        return FileResponse("static/favicon.ico")
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


# ──────────────────────────────────────────
# WEBHOOK — ресторанный бот (Multi-Tenant)
# ──────────────────────────────────────────
@app.post("/webhook/{slug}")
@limiter.limit("300/minute")  # защита от webhook flooding
def restaurant_webhook(
    request: Request,
    slug: str,
    update: dict,
    x_telegram_bot_api_secret_token: str = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
):
    """
    Принимает обновления от Telegram для бота конкретного ресторана.

    Безопасность: X-Telegram-Bot-Api-Secret-Token проверяется против
    WEBHOOK_SECRET. Запросы без валидного секрета → 403.
    Всегда возвращает 200 при успешной авторизации (требование Telegram).
    """
    if x_telegram_bot_api_secret_token != settings.WEBHOOK_SECRET:
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
    """
    Принимает обновления от Telegram для платформенного бота.
    """
    if x_telegram_bot_api_secret_token != settings.WEBHOOK_SECRET:
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
