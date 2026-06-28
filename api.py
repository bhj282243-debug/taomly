"""
api.py — Taomly Platform
Точка входа FastAPI приложения.

Изменения v2:
  - /health: утечка соединения закрыта через контекстный менеджер with SessionLocal()
  - /webhook: добавлена проверка X-Telegram-Bot-Api-Secret-Token —
    запросы без валидного секрета отклоняются с 403
  - lifespan: set_webhook передаёт secret_token → Telegram будет подписывать запросы
  - StaticFiles: проверяем существование папки static/ перед монтированием
  - WEBHOOK_SECRET генерируется из SECRET_KEY если не задан отдельно
  - create_all() оставлен для MVP, комментарий о миграциях добавлен
"""

import hashlib
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import handlers
import models
import telebot
from database import SessionLocal, engine
from routers import agency, menu, orders, reservations, restaurants, waiter_calls

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Секрет для верификации webhook-запросов от Telegram.
# Telegram передаёт его в заголовке X-Telegram-Bot-Api-Secret-Token.
# Если WEBHOOK_SECRET не задан явно — деривируем из SECRET_KEY.
# Telegram принимает только [A-Za-z0-9_-], длина 1–256.
_SECRET_KEY = os.getenv("SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    hashlib.sha256(_SECRET_KEY.encode()).hexdigest()[:64],
)


# ──────────────────────────────────────────
# LIFESPAN
# ──────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # MVP: create_all создаёт таблицы если не существуют.
    # TODO: заменить на Alembic-миграции перед масштабированием —
    # create_all не применяет изменения к уже существующим таблицам.
    models.Base.metadata.create_all(bind=engine)
    logger.info("БД инициализирована")

    # Устанавливаем webhook для платформенного бота
    if handlers.platform_bot:
        try:
            handlers.platform_bot.remove_webhook()
            if WEBHOOK_URL:
                handlers.platform_bot.set_webhook(
                    url=f"{WEBHOOK_URL}/webhook",
                    # Telegram будет подписывать каждый запрос этим секретом
                    secret_token=WEBHOOK_SECRET,
                )
                logger.info("Webhook установлен: %s/webhook", WEBHOOK_URL)
            else:
                logger.warning("WEBHOOK_URL не задан — webhook не установлен")
        except Exception:
            logger.exception(
                "Не удалось установить webhook — приложение продолжает работу"
            )
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
    version="2.0.0",
    lifespan=lifespan,
)

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
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
else:
    logger.warning("Папка static/ не найдена — статические файлы не будут отдаваться")


# ──────────────────────────────────────────
# СЛУЖЕБНЫЕ ЭНДПОИНТЫ
# ──────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "running", "app": "Taomly", "version": "2.0.0"}


@app.get("/health")
def health():
    """
    Проверка состояния приложения.
    Используется Render для health check — возвращает 503 если БД недоступна.
    Соединение гарантированно закрывается через контекстный менеджер.
    """
    try:
        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
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


# ──────────────────────────────────────────
# WEBHOOK — платформенный бот
# ──────────────────────────────────────────
@app.post("/webhook")
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

    Безопасность:
      - Проверяет заголовок X-Telegram-Bot-Api-Secret-Token.
        Telegram отправляет его при каждом запросе если secret_token был
        передан при set_webhook(). Запросы без валидного секрета → 403.
      - Всегда возвращает 200 при успешной авторизации — Telegram требует 200,
        иначе будет повторять запрос до 10 раз.
    """
    # Проверяем секрет — защита от посторонних POST-запросов на /webhook
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        logger.warning(
            "Webhook: отклонён запрос с невалидным секретом от %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    if not handlers.platform_bot:
        return {"ok": False, "detail": "Платформенный бот не настроен"}

    try:
        update_obj = telebot.types.Update.de_json(update)
        handlers.platform_bot.process_new_updates([update_obj])
        return {"ok": True}
    except Exception:
        logger.exception("Ошибка обработки webhook update")
        # 200 чтобы Telegram не повторял запрос
        return {"ok": False}
