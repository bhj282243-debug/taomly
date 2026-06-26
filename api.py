import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from database import engine
import models
import telebot
from routers import menu, orders, reservations, waiter_calls, restaurants
from routers import agency
import handlers

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")

bot = handlers.bot


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=engine)
    bot.remove_webhook()
    if WEBHOOK_URL:
        bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        logger.info(f"Webhook установлен: {WEBHOOK_URL}/webhook")
    else:
        logger.warning("WEBHOOK_URL not configured")
    yield
    bot.remove_webhook()


app = FastAPI(
    title="Taomly White Label Platform",
    description="Multi-tenant restaurant SaaS engine",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(menu.router, prefix="/api/menu", tags=["menu"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
app.include_router(reservations.router, prefix="/api/reservations", tags=["reservations"])
app.include_router(waiter_calls.router, prefix="/api/waiter-calls", tags=["waiter-calls"])
app.include_router(restaurants.router)
app.include_router(agency.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return {"status": "running", "app": "Taomly", "version": "2.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/app")
def serve_app():
    return FileResponse("static/index.html")


@app.get("/admin")
def serve_admin():
    return FileResponse("static/admin.html")


@app.get("/agency-admin")
def serve_agency_admin():
    return FileResponse("static/agency_admin.html")


@app.post("/webhook")
def webhook(update: dict):
    try:
        update_obj = telebot.types.Update.de_json(update)
        bot.process_new_updates([update_obj])
        return {"ok": True}
    except Exception as e:
        logger.exception("Webhook error")
        return {"ok": False}
