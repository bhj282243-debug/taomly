import os
import logging
import telebot
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from database import engine
import models

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
bot = telebot.TeleBot(BOT_TOKEN)

# TODO: replace with Alembic migrations
models.Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    bot.remove_webhook()
    if WEBHOOK_URL:
        bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    else:
        logger.warning("WEBHOOK_URL not configured")
    yield
    bot.remove_webhook()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return {"status": "running", "app": "Taomly", "version": "0.1.0"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/app")
def serve_app():
    return FileResponse("static/index.html")

@app.post("/webhook")
def webhook(update: dict):
    try:
        update_obj = telebot.types.Update.de_json(update)
        bot.process_new_updates([update_obj])
        return {"ok": True}
    except Exception as e:
        logger.exception("Webhook error")
        return {"ok": False}
