"""
handlers.py — Taomly Platform

Изменения v2:
  - Добавлен BOT_CACHE: dict — один TeleBot на ресторан, создаётся один раз.
    Устраняет создание сотен объектов при нагрузке.
  - decrypt_token вызывается только при первом создании бота для ресторана.
  - notify_new_order: улучшен лог — добавлен restaurant.name для читаемости.
  - notify_client_accepted: принимает restaurant вторым аргументом (Multi-Tenant).

Изменения v3 (Security):
  - BOT_CACHE: задокументировано ограничение multi-worker.
"""

import logging
from typing import Dict

from config import settings

import telebot

from auth import decrypt_token
from i18n import t as _t
from utils import format_price as _fmt_price

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# ПЛАТФОРМЕННЫЙ БОТ (Agency / onboarding)
# ──────────────────────────────────────────
_PLATFORM_BOT_TOKEN = settings.BOT_TOKEN or None
platform_bot = telebot.TeleBot(_PLATFORM_BOT_TOKEN) if _PLATFORM_BOT_TOKEN else None

# ──────────────────────────────────────────
# КЭШ БОТОВ — один объект TeleBot на ресторан
# Ключ: restaurant.id → TeleBot
# При текущем масштабе (Render Free, один воркер) dict достаточен.
# ──────────────────────────────────────────
_BOT_CACHE: Dict[int, telebot.TeleBot] = {}
# ⚠️  АРХИТЕКТУРНОЕ ОГРАНИЧЕНИЕ:
#     _BOT_CACHE — процесс-локальный dict. Работает корректно только при
#     одном воркере uvicorn (--workers 1, текущая конфигурация).
#
#     При горизонтальном масштабировании (2+ инстансов Render / 2+ воркеров):
#       - Каждый процесс имеет свой _BOT_CACHE.
#       - invalidate_bot_cache() на инстансе A не очистит кэш на инстансе B.
#       - Результат: бот одного ресторана может отправлять через старый токен.
#
#     Решение при масштабировании (этап 2):
#       - Перенести токены в Redis (TTL 1 час).
#       - Читать из Redis при каждом notify_* вызове (с in-memory LRU как L1).
#
#     До масштабирования: держать workers=1 в Dockerfile (текущая конфигурация).


def get_restaurant_bot(restaurant) -> telebot.TeleBot:
    """
    Возвращает TeleBot для конкретного ресторана.

    При первом вызове: расшифровывает токен и создаёт TeleBot, кладёт в кэш.
    При повторных вызовах: возвращает из кэша без расшифровки.

    Args:
        restaurant: объект Restaurant с telegram_bot_token_encrypted

    Raises:
        ValueError если токен не настроен
    """
    if restaurant.id in _BOT_CACHE:
        return _BOT_CACHE[restaurant.id]

    if not restaurant.telegram_bot_token_encrypted:
        logger.warning(
            "Ресторан «%s» (id=%s): Telegram Bot Token не настроен",
            restaurant.name,
            restaurant.id,
        )
        raise ValueError(
            f"Telegram Bot не настроен для ресторана «{restaurant.name}»"
        )

    # decrypt_token вызывается только один раз — при первом создании бота
    bot_token = decrypt_token(restaurant.telegram_bot_token_encrypted)
    bot = telebot.TeleBot(bot_token)
    _BOT_CACHE[restaurant.id] = bot

    logger.info(
        "TeleBot создан и закэширован для ресторана «%s» (id=%s)",
        restaurant.name,
        restaurant.id,
    )
    return bot


def invalidate_bot_cache(restaurant_id: int) -> None:
    """
    Сбрасывает кэш бота для ресторана.

    Вызывать при смене telegram_bot_token в настройках ресторана,
    иначе старый бот останется в кэше до перезапуска сервера.
    """
    if restaurant_id in _BOT_CACHE:
        del _BOT_CACHE[restaurant_id]
        logger.info("BOT_CACHE сброшен для restaurant_id=%s", restaurant_id)


# ──────────────────────────────────────────
# WEBHOOK_URL VALIDATION
# ──────────────────────────────────────────
def _validate_webhook_url(url: str) -> str:
    """
    Проверяет что WEBHOOK_URL является абсолютным HTTPS base URL
    без trailing path (допустим только trailing slash).

    Корректно:   https://example.com
    Корректно:   https://example.com/
    Некорректно: http://example.com     (не HTTPS)
    Некорректно: https://example.com/webhook  (лишний path)
    Некорректно: https://example.com/app      (лишний path)

    Returns: нормализованный URL без trailing slash.
    Raises: ValueError с понятным сообщением.
    """
    url = url.strip()
    if not url.startswith("https://"):
        raise ValueError(
            f"WEBHOOK_URL must be an absolute HTTPS base URL, "
            f'e.g. https://your-app.onrender.com — got: {url!r}. '
            f"Make sure it starts with https:// and has no path suffix."
        )
    # Убираем trailing slash для нормализации
    normalized = url.rstrip("/")
    # Проверяем что нет лишнего path (допустима только схема + хост + опциональный порт)
    from urllib.parse import urlparse
    parsed = urlparse(normalized)
    if parsed.path and parsed.path != "/":
        raise ValueError(
            f"WEBHOOK_URL must be a base URL without path suffix — "
            f"got {url!r} (path: {parsed.path!r}). "
            f"Correct example: https://your-app.onrender.com"
        )
    return normalized


# ──────────────────────────────────────────
# /start ДЛЯ РЕСТОРАННЫХ БОТОВ (Multi-Tenant)
# ──────────────────────────────────────────
# APP_BASE_URL — базовый URL Mini App (например: https://your-app.onrender.com/app).
# Используется ресторанными и платформенным ботом для WebAppInfo кнопок.
# WEBHOOK_URL = base domain деплоя. Код дописывает /webhook, /webhook/{slug}, /app.
# Если platform bot активен и WEBHOOK_URL некорректен — стартап прерывается с ошибкой.
if platform_bot and not settings.WEBHOOK_URL:
    raise RuntimeError(
        "[STARTUP ERROR] BOT_TOKEN задан (platform bot активен), "
        "но WEBHOOK_URL отсутствует. "
        "Mini App кнопки в Telegram будут нерабочими. "
        "Задайте: WEBHOOK_URL=https://your-app-domain.com"
    )

if platform_bot and settings.WEBHOOK_URL:
    try:
        _validate_webhook_url(settings.WEBHOOK_URL)
    except ValueError as _exc:
        raise RuntimeError(f"[STARTUP ERROR] Некорректный WEBHOOK_URL: {_exc}") from _exc

_APP_BASE_URL = (settings.WEBHOOK_URL or "").rstrip("/") + "/app"


def _send_restaurant_welcome(bot: telebot.TeleBot, chat_id: int, restaurant) -> None:
    """Отправляет приветствие и кнопку Mini App конкретного ресторана."""
    app_url = f"{_APP_BASE_URL}?slug={restaurant.slug}"
    welcome_text = restaurant.welcome_text or "🌟 Xush kelibsiz!"

    reply_markup = telebot.types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        is_persistent=True,
    )
    reply_markup.add(
        telebot.types.KeyboardButton(
            text="🍽️  MENYUNI OCHISH  🍽️",
            web_app=telebot.types.WebAppInfo(url=app_url),
        )
    )

    inline_markup = telebot.types.InlineKeyboardMarkup()
    inline_markup.add(
        telebot.types.InlineKeyboardButton(
            text="🍽️  Menyuni ochish  →",
            web_app=telebot.types.WebAppInfo(url=app_url),
        )
    )

    bot.send_message(
        chat_id,
        f"{welcome_text}\n\n"
        f"🍽️ {restaurant.name} — mazali taomlar buyurtma qiling\n"
        "⚡️ Tez va qulay — bir necha soniyada\n"
        "🚀 Quyidagi tugmani bosing:",
        reply_markup=reply_markup,
    )
    bot.send_message(chat_id, "👇", reply_markup=inline_markup)


def process_restaurant_webhook_update(restaurant, update_dict: dict) -> None:
    """
    Обрабатывает входящий Telegram Update для конкретного ресторанного бота.

    Вызывается из эндпоинта POST /webhook/{slug} в api.py.
    """
    bot = get_restaurant_bot(restaurant)

    if not getattr(bot, "_taomly_handlers_registered", False):

        @bot.message_handler(commands=["start"])
        def _handle_start(message, _restaurant=restaurant, _bot=bot):
            _send_restaurant_welcome(_bot, message.chat.id, _restaurant)

        @bot.message_handler(func=lambda m: m.text and "MENYUNI OCHISH" in m.text)
        def _handle_menu_button(message, _restaurant=restaurant, _bot=bot):
            _send_restaurant_welcome(_bot, message.chat.id, _restaurant)

        bot._taomly_handlers_registered = True

    update_obj = telebot.types.Update.de_json(update_dict)
    bot.process_new_updates([update_obj])


# ──────────────────────────────────────────
# ПЛАТФОРМЕННЫЙ /start (onboarding)
# ──────────────────────────────────────────
if platform_bot:
    @platform_bot.message_handler(commands=["start"])
    def handle_start(message):
        """Приветствие с кнопкой открытия меню (платформенный бот)."""
        reply_markup = telebot.types.ReplyKeyboardMarkup(
            resize_keyboard=True,
            is_persistent=True,
        )
        reply_markup.add(
            telebot.types.KeyboardButton(
                text="🍽️  MENYUNI OCHISH  🍽️",
                web_app=telebot.types.WebAppInfo(url=_APP_BASE_URL),
            )
        )

        inline_markup = telebot.types.InlineKeyboardMarkup()
        inline_markup.add(
            telebot.types.InlineKeyboardButton(
                text="🍽️  Menyuni ochish  →",
                web_app=telebot.types.WebAppInfo(url=_APP_BASE_URL),
            )
        )

        platform_bot.send_message(
            message.chat.id,
            "🌟 Xush kelibsiz!\n\n"
            "🍽️ Mazali taomlar buyurtma qiling\n"
            "⚡️ Tez va qulay — bir necha soniyada\n"
            "🚀 Quyidagi tugmani bosing:",
            reply_markup=reply_markup,
        )
        platform_bot.send_message(
            message.chat.id,
            "👇",
            reply_markup=inline_markup,
        )

    @platform_bot.message_handler(func=lambda m: "MENYUNI OCHISH" in m.text)
    def handle_menu_button(message):
        """Обработка нажатия на постоянную кнопку меню."""
        inline_markup = telebot.types.InlineKeyboardMarkup()
        inline_markup.add(
            telebot.types.InlineKeyboardButton(
                text="🍽️  Menyuni ochish  →",
                web_app=telebot.types.WebAppInfo(url=_APP_BASE_URL),
            )
        )
        platform_bot.send_message(
            message.chat.id,
            "👇 Menyuni ochish uchun bosing:",
            reply_markup=inline_markup,
        )


# ──────────────────────────────────────────
# УВЕДОМЛЕНИЕ ДИСПЕТЧЕРУ — новый заказ
# ──────────────────────────────────────────
def notify_new_order(order, items, restaurant, location=None) -> None:
    """
    Отправляет уведомление диспетчеру ресторана о новом заказе.

    Multi-Tenant: dispatcher_id и бот берутся из объекта restaurant.
    Вызывается через BackgroundTasks — не блокирует HTTP-ответ.

    S1-7: location — опциональный параметр; если передан, currency берётся
    из Location (source of truth). Если не передан — fallback на restaurant.currency
    для backward compat со старыми вызовами (например, тесты без location).
    """
    dispatcher_id = restaurant.telegram_dispatcher_id
    if not dispatcher_id:
        logger.warning(
            "Ресторан «%s» (id=%s): telegram_dispatcher_id не настроен — "
            "уведомление о заказе #%s не отправлено",
            restaurant.name,
            restaurant.id,
            order.id,
        )
        return

    order_type_labels = {
        "delivery": "🛵 Yetkazib berish",
        "takeaway": "🥡 Olib ketish",
        "dine_in":  "🍽️ Zal (stol)",
    }
    type_label = order_type_labels.get(order.order_type, order.order_type)

    # S1-7: currency из Location если передана, иначе fallback на Restaurant
    _settings = location if location is not None else restaurant
    _cur = getattr(_settings, "currency", None) or "UZS"
    items_text = "".join(
        f"  • {item.name} × {item.quantity} — {_fmt_price(item.price * item.quantity, _cur)}\n"
        for item in items
    )

    location_text = ""
    if order.order_type == "delivery" and order.address:
        location_text = f"📍 Manzil: {order.address}\n"
    elif order.order_type == "dine_in" and order.table_id:
        location_text = f"🪑 Stol: #{order.table_id}\n"

    comment_text = f"💬 Izoh: {order.comment}\n" if order.comment else ""

    client_text = ""
    if order.client_name:
        client_text += f"👤 {order.client_name}\n"
    if order.client_phone:
        client_text += f"📞 {order.client_phone}\n"

    text = (
        f"🔔 YANGI BUYURTMA #{order.id}\n"
        f"{'─' * 28}\n"
        f"{type_label}\n"
        f"{client_text}"
        f"{location_text}"
        f"{comment_text}"
        f"{'─' * 28}\n"
        f"{items_text}"
        f"{'─' * 28}\n"
        f"💰 Jami: {_fmt_price(int(order.total_amount), _cur)}"
    )

    try:
        bot = get_restaurant_bot(restaurant)
        bot.send_message(dispatcher_id, text)
        logger.info(
            "Уведомление о заказе #%s → диспетчер %s (ресторан «%s» id=%s)",
            order.id,
            dispatcher_id,
            restaurant.name,
            restaurant.id,
        )
    except ValueError as e:
        logger.warning("notify_new_order: %s", e)
    except Exception:
        logger.exception(
            "Ошибка отправки уведомления диспетчеру: заказ #%s ресторан «%s» id=%s",
            order.id,
            restaurant.name,
            restaurant.id,
        )


# ──────────────────────────────────────────
# УВЕДОМЛЕНИЕ КЛИЕНТУ — заказ принят
# ──────────────────────────────────────────
# ──────────────────────────────────────────
# ХЕЛПЕР — отправка уведомлений клиенту
# ──────────────────────────────────────────

def _notify_client(order, restaurant, text: str, event_name: str) -> None:
    """
    Общая логика отправки Telegram-уведомления клиенту о смене статуса заказа.

    Вызывается из публичных notify_client_* через BackgroundTasks — не блокирует
    HTTP-ответ. Публичные функции отвечают за формирование текста сообщения,
    этот хелпер — за отправку и обработку ошибок.

    Если нужно добавить retry, таймаут или метрики — менять только здесь.
    """
    if not order.client_telegram_id:
        logger.warning(
            "%s: заказ #%s не имеет client_telegram_id",
            event_name, order.id,
        )
        return
    try:
        bot = get_restaurant_bot(restaurant)
        bot.send_message(order.client_telegram_id, text)
        logger.info(
            "%s: заказ #%s клиент %s ресторан «%s»",
            event_name, order.id, order.client_telegram_id, restaurant.name,
        )
    except ValueError as e:
        logger.warning("%s: %s", event_name, e)
    except Exception:
        logger.exception(
            "Ошибка %s: заказ #%s клиент %s ресторан «%s»",
            event_name, order.id, order.client_telegram_id, restaurant.name,
        )


def notify_client_accepted(order, restaurant) -> None:
    """
    Клиенту: заказ принят рестораном.

    Multi-Tenant: использует бот конкретного ресторана.
    Вызывается через BackgroundTasks — не блокирует HTTP-ответ.
    """
    lang = getattr(restaurant, "language", "uz") or "uz"
    order_type = getattr(order, "order_type", None) or "default"
    action_key = f"telegram.action.{order_type}"
    action = _t(action_key, lang)
    if action == action_key:  # ключ не найден → fallback
        action = _t("telegram.action.default", lang)
    text = _t(
        "telegram.order_accepted",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(restaurant, "currency", None) or "UZS"),
        action=action,
    )
    _notify_client(order, restaurant, text, "notify_client_accepted")


def notify_client_preparing(order, restaurant) -> None:
    """Клиенту: заказ готовится."""
    lang = getattr(restaurant, "language", "uz") or "uz"
    text = _t(
        "telegram.order_preparing",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(restaurant, "currency", None) or "UZS"),
    )
    _notify_client(order, restaurant, text, "notify_client_preparing")


def notify_client_ready(order, restaurant) -> None:
    """Клиенту: заказ готов."""
    lang = getattr(restaurant, "language", "uz") or "uz"
    order_type = getattr(order, "order_type", None) or "default"
    detail_key = f"telegram.ready_detail.{order_type}"
    detail = _t(detail_key, lang)
    if detail == detail_key:  # ключ не найден → fallback
        detail = _t("telegram.ready_detail.default", lang)
    text = _t(
        "telegram.order_ready",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(restaurant, "currency", None) or "UZS"),
        detail=detail,
    )
    _notify_client(order, restaurant, text, "notify_client_ready")


def notify_client_delivering(order, restaurant) -> None:
    """Клиенту: курьер в пути."""
    lang = getattr(restaurant, "language", "uz") or "uz"
    text = _t(
        "telegram.order_delivering",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(restaurant, "currency", None) or "UZS"),
    )
    _notify_client(order, restaurant, text, "notify_client_delivering")


def notify_client_completed(order, restaurant) -> None:
    """Клиенту: заказ доставлен / завершён."""
    lang = getattr(restaurant, "language", "uz") or "uz"
    text = _t(
        "telegram.order_completed",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(restaurant, "currency", None) or "UZS"),
    )
    _notify_client(order, restaurant, text, "notify_client_completed")


def notify_client_cancelled(order, restaurant, comment: str = "") -> None:
    """Клиенту: заказ отменён."""
    lang = getattr(restaurant, "language", "uz") or "uz"
    if comment and comment.strip():
        reason = _t("telegram.cancelled_reason", lang, comment=comment.strip())
    else:
        reason = ""
    text = _t(
        "telegram.order_cancelled",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(restaurant, "currency", None) or "UZS"),
        reason=reason,
    )
    _notify_client(order, restaurant, text, "notify_client_cancelled")
