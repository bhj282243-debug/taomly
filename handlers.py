"""
handlers.py — Taomly Platform

Изменения v5 (Phase 12: Location = единственный source of truth):
  - get_restaurant_bot() alias удалён (был deprecated, не использовался production кодом).
  - _notify_client: добавлен параметр location=None. Bot берётся из get_location_bot(location).
    Fallback на restaurant сохранён для тестовой совместимости (location=None).
  - notify_client_*: передают location явно в _notify_client (пятый аргумент).

Изменения v4 (S1-8: Telegram Credentials Migration to Location):
  - get_location_bot(location): читает location.telegram_bot_token_encrypted.
    Кэш: _BOT_CACHE[location.id].
  - invalidate_bot_cache(location_id): принимает location_id.
  - notify_new_order: dispatcher_id из location.telegram_dispatcher_id.
    bot через get_location_bot(location).
  - notify_client_*: принимают опциональный location.
    language/currency берутся из location если передан, иначе fallback на restaurant.
  - process_restaurant_webhook_update: принимает location, bot через get_location_bot.

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
from typing import Dict, Optional

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
# КЭШ БОТОВ — один объект TeleBot на Location
# Ключ: location.id → TeleBot  (S1-8: был restaurant.id)
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
#       - Результат: бот одной локации может отправлять через старый токен.
#
#     Решение при масштабировании (этап 2):
#       - Перенести токены в Redis (TTL 1 час).
#       - Читать из Redis при каждом notify_* вызове (с in-memory LRU как L1).
#
#     До масштабирования: держать workers=1 в Dockerfile (текущая конфигурация).


def get_location_bot(location) -> telebot.TeleBot:
    """
    Возвращает TeleBot для конкретной Location.

    S1-8: source of truth = location.telegram_bot_token_encrypted.
    Кэш ключ = location.id (был restaurant.id до S1-8).

    При первом вызове: расшифровывает токен и создаёт TeleBot, кладёт в кэш.
    При повторных вызовах: возвращает из кэша без расшифровки.

    Args:
        location: объект с telegram_bot_token_encrypted и id (Location или Restaurant)

    Raises:
        ValueError если токен не настроен
    """
    cache_key = location.id

    if cache_key in _BOT_CACHE:
        return _BOT_CACHE[cache_key]

    token_attr = getattr(location, "telegram_bot_token_encrypted", None)
    if not token_attr:
        obj_name = getattr(location, "name", repr(location))
        logger.warning(
            "Location/Restaurant «%s» (id=%s): Telegram Bot Token не настроен",
            obj_name,
            location.id,
        )
        raise ValueError(
            f"Telegram Bot не настроен для «{obj_name}»"
        )

    bot_token = decrypt_token(token_attr)
    bot = telebot.TeleBot(bot_token)
    _BOT_CACHE[cache_key] = bot

    logger.info(
        "TeleBot создан и закэширован для id=%s",
        location.id,
    )
    return bot



def invalidate_bot_cache(location_id: int) -> None:
    """
    Сбрасывает кэш бота для Location.

    S1-8: принимает location_id (был restaurant_id до S1-8).
    Кэш ключ = location_id.

    Вызывать при смене telegram_bot_token в настройках Location,
    иначе старый бот останется в кэше до перезапуска сервера.
    """
    if location_id in _BOT_CACHE:
        del _BOT_CACHE[location_id]
        logger.info("BOT_CACHE сброшен для location_id=%s", location_id)


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


def process_restaurant_webhook_update(restaurant, update_dict: dict, location=None) -> None:
    """
    Обрабатывает входящий Telegram Update для конкретного ресторанного бота.

    Вызывается из эндпоинта POST /webhook/{slug} в api.py.

    S1-8: принимает опциональный location.
    Если location передан — bot получается через get_location_bot(location).
    Fallback: get_location_bot(restaurant) для backward compat.
    """
    _bot_source = location if location is not None else restaurant
    bot = get_location_bot(_bot_source)

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
def notify_new_order(order, items, restaurant, location=None, table_number=None) -> None:
    """
    Отправляет уведомление диспетчеру ресторана о новом заказе.

    S1-8: dispatcher_id и бот берутся из Location (source of truth).
    Если location не передан — fallback на restaurant для backward compat
    со старыми вызовами (legacy тесты).

    Phase 14: добавлены поля:
      - table_number (human-readable, вместо table_id)
      - delivery_fee отдельной строкой (если > 0)
      - scheduled_at (для запланированных заказов)

    Вызывается через BackgroundTasks — не блокирует HTTP-ответ.
    """
    # S1-8: source of truth = location; fallback на restaurant для backward compat
    _src = location if location is not None else restaurant

    dispatcher_id = getattr(_src, "telegram_dispatcher_id", None)
    if not dispatcher_id:
        logger.warning(
            "Location/Restaurant (id=%s): telegram_dispatcher_id не настроен — "
            "уведомление о заказе #%s не отправлено",
            getattr(_src, "id", "?"),
            order.id,
        )
        return

    order_type_labels = {
        "delivery": "🛵 Yetkazib berish",
        "takeaway": "🥡 Olib ketish",
        "dine_in":  "🍽️ Zal (stol)",
    }
    type_label = order_type_labels.get(order.order_type, order.order_type)

    # S1-8: currency из Location (source of truth), fallback на restaurant
    _cur = getattr(_src, "currency", None) or "UZS"
    items_text = "".join(
        f"  • {item.name} × {item.quantity} — {_fmt_price(item.price * item.quantity, _cur)}\n"
        for item in items
    )

    location_text = ""
    if order.order_type == "delivery" and order.address:
        location_text = f"📍 Manzil: {order.address}\n"
    elif order.order_type == "dine_in":
        # Phase 14: use table_number (human-readable) instead of table_id (DB ID)
        _tnum = table_number or (f"#{order.table_id}" if order.table_id else None)
        if _tnum:
            location_text = f"🪑 Stol: {_tnum}\n"

    comment_text = f"💬 Izoh: {order.comment}\n" if order.comment else ""

    client_text = ""
    if order.client_name:
        client_text += f"👤 {order.client_name}\n"
    if order.client_phone:
        client_text += f"📞 {order.client_phone}\n"

    # Phase 14: scheduled_at display
    scheduled_text = ""
    if getattr(order, "scheduled_at", None):
        try:
            import pytz
            tz_str = getattr(_src, "timezone", None) or "Asia/Tashkent"
            tz = pytz.timezone(tz_str)
            local_time = order.scheduled_at.astimezone(tz)
            scheduled_text = f"⏰ Vaqt: {local_time.strftime('%d.%m %H:%M')}\n"
        except Exception:
            scheduled_text = f"⏰ Vaqt: {order.scheduled_at.strftime('%d.%m %H:%M')}\n"

    # Phase 14: delivery fee breakdown (subtotal + fee if fee > 0)
    _subtotal = getattr(order, "subtotal", order.total_amount)
    _fee = getattr(order, "delivery_fee", 0) or 0
    if _fee > 0:
        financial_text = (
            f"💰 Buyurtma: {_fmt_price(_subtotal, _cur)}\n"
            f"🚗 Yetkazish: {_fmt_price(_fee, _cur)}\n"
            f"💰 Jami: {_fmt_price(int(order.total_amount), _cur)}"
        )
    else:
        financial_text = f"💰 Jami: {_fmt_price(int(order.total_amount), _cur)}"

    text = (
        f"🔔 YANGI BUYURTMA #{order.id}\n"
        f"{'─' * 28}\n"
        f"{type_label}\n"
        f"{client_text}"
        f"{location_text}"
        f"{scheduled_text}"
        f"{comment_text}"
        f"{'─' * 28}\n"
        f"{items_text}"
        f"{'─' * 28}\n"
        f"{financial_text}"
    )

    try:
        bot = get_location_bot(_src)
        bot.send_message(dispatcher_id, text)
        logger.info(
            "Уведомление о заказе #%s → диспетчер %s (id=%s)",
            order.id,
            dispatcher_id,
            getattr(_src, "id", "?"),
        )
    except ValueError as e:
        logger.warning("notify_new_order: %s", e)
    except Exception:
        logger.exception(
            "Ошибка отправки уведомления диспетчеру: заказ #%s id=%s",
            order.id,
            getattr(_src, "id", "?"),
        )


# ──────────────────────────────────────────
# ХЕЛПЕР — отправка уведомлений клиенту
# ──────────────────────────────────────────

def _notify_client(order, restaurant, text: str, event_name: str, location=None) -> None:
    """
    Общая логика отправки Telegram-уведомления клиенту о смене статуса заказа.

    Phase 12: принимает location=None как пятый аргумент.
    Bot берётся из get_location_bot(location) если location передан,
    иначе fallback на get_location_bot(restaurant) для тестовой совместимости
    (test_i18n_notifications.py вызывает notify_client_* без location).

    Production notify_client_* всегда передают location явно.

    Вызывается через BackgroundTasks — не блокирует HTTP-ответ.
    language/currency уже применены снаружи в notify_client_*.

    Если нужно добавить retry, таймаут или метрики — менять только здесь.
    """
    if not order.client_telegram_id:
        logger.warning(
            "%s: заказ #%s не имеет client_telegram_id",
            event_name, order.id,
        )
        return
    try:
        _bot_src = location if location is not None else restaurant
        bot = get_location_bot(_bot_src)
        bot.send_message(order.client_telegram_id, text)
        logger.info(
            "%s: заказ #%s клиент %s",
            event_name, order.id, order.client_telegram_id,
        )
    except ValueError as e:
        logger.warning("%s: %s", event_name, e)
    except Exception:
        logger.exception(
            "Ошибка %s: заказ #%s клиент %s",
            event_name, order.id, order.client_telegram_id,
        )


def notify_client_accepted(order, restaurant, location=None) -> None:
    """
    Клиенту: заказ принят рестораном.

    S1-8: location опциональный.
    language/currency берутся из location если передан, иначе fallback на restaurant.
    (backward compat: test_i18n_notifications вызывает без location)
    Вызывается через BackgroundTasks — не блокирует HTTP-ответ.
    """
    _src = location if location is not None else restaurant
    lang = getattr(_src, "language", "uz") or "uz"
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
        amount=_fmt_price(int(order.total_amount), getattr(_src, "currency", None) or "UZS"),
        action=action,
    )
    _notify_client(order, restaurant, text, "notify_client_accepted", location)


def notify_client_preparing(order, restaurant, location=None) -> None:
    """Клиенту: заказ готовится."""
    _src = location if location is not None else restaurant
    lang = getattr(_src, "language", "uz") or "uz"
    text = _t(
        "telegram.order_preparing",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(_src, "currency", None) or "UZS"),
    )
    _notify_client(order, restaurant, text, "notify_client_preparing", location)


def notify_client_ready(order, restaurant, location=None) -> None:
    """Клиенту: заказ готов."""
    _src = location if location is not None else restaurant
    lang = getattr(_src, "language", "uz") or "uz"
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
        amount=_fmt_price(int(order.total_amount), getattr(_src, "currency", None) or "UZS"),
        detail=detail,
    )
    _notify_client(order, restaurant, text, "notify_client_ready", location)


def notify_client_delivering(order, restaurant, location=None) -> None:
    """Клиенту: курьер в пути."""
    _src = location if location is not None else restaurant
    lang = getattr(_src, "language", "uz") or "uz"
    text = _t(
        "telegram.order_delivering",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(_src, "currency", None) or "UZS"),
    )
    _notify_client(order, restaurant, text, "notify_client_delivering", location)


def notify_client_completed(order, restaurant, location=None) -> None:
    """Клиенту: заказ доставлен / завершён."""
    _src = location if location is not None else restaurant
    lang = getattr(_src, "language", "uz") or "uz"
    text = _t(
        "telegram.order_completed",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(_src, "currency", None) or "UZS"),
    )
    _notify_client(order, restaurant, text, "notify_client_completed", location)


def notify_client_cancelled(order, restaurant, comment: str = "", location=None) -> None:
    """Клиенту: заказ отменён."""
    _src = location if location is not None else restaurant
    lang = getattr(_src, "language", "uz") or "uz"
    if comment and comment.strip():
        reason = _t("telegram.cancelled_reason", lang, comment=comment.strip())
    else:
        reason = ""
    text = _t(
        "telegram.order_cancelled",
        lang,
        separator="─" * 28,
        id=order.id,
        amount=_fmt_price(int(order.total_amount), getattr(_src, "currency", None) or "UZS"),
        reason=reason,
    )
    _notify_client(order, restaurant, text, "notify_client_cancelled", location)


# ──────────────────────────────────────────
# Phase 14: SCHEDULED ORDER CONFIRMATION
# ──────────────────────────────────────────

def notify_client_scheduled(order, restaurant, location=None) -> None:
    """
    Phase 14 (BLOCK-01): Confirmation notification for scheduled orders.
    Sent at creation (status=new). notify_client_accepted() sent at activation.
    Uses existing notification architecture (_notify_client, _t, i18n).
    Displays scheduled_at in local timezone.
    """
    _src = location if location is not None else restaurant
    lang = getattr(_src, "language", "uz") or "uz"
    _cur = getattr(_src, "currency", None) or "UZS"

    # Format scheduled_at in location's timezone
    scheduled_local = ""
    if getattr(order, "scheduled_at", None):
        try:
            import pytz
            tz_str = getattr(_src, "timezone", None) or "Asia/Tashkent"
            tz = pytz.timezone(tz_str)
            local_time = order.scheduled_at.astimezone(tz)
            scheduled_local = local_time.strftime("%d.%m %H:%M")
        except Exception:
            if order.scheduled_at:
                scheduled_local = order.scheduled_at.strftime("%d.%m %H:%M")

    # Use i18n key if exists, else fallback text
    try:
        text = _t(
            "telegram.order_scheduled",
            lang,
            id=order.id,
            scheduled_at=scheduled_local,
            amount=_fmt_price(int(order.total_amount), _cur),
        )
    except Exception:
        # Graceful fallback if i18n key not yet added
        text = (
            f"📅 Buyurtmangiz qabul qilindi! #{order.id}\n"
            f"⏰ Rejadagi vaqt: {scheduled_local}\n"
            f"💰 Jami: {_fmt_price(int(order.total_amount), _cur)}"
        )

    _notify_client(order, restaurant, text, "notify_client_scheduled", location)


# ──────────────────────────────────────────
# Phase 14: WAITER CALL NOTIFICATION
# ──────────────────────────────────────────

def notify_waiter_call(call, table, location) -> None:
    """
    Phase 14: Sends waiter call notification to restaurant dispatcher.
    Called via BackgroundTasks — does not block HTTP response.

    Source of truth for bot: location (ADR-001: 1 Location = 1 Bot).
    Graceful failure: logs warning, never raises (non-critical notification).
    """
    dispatcher_id = getattr(location, "telegram_dispatcher_id", None)
    if not dispatcher_id:
        logger.warning(
            "notify_waiter_call: dispatcher_id не настроен location_id=%s call_id=%s",
            getattr(location, "id", "?"),
            getattr(call, "id", "?"),
        )
        return

    # Human-readable table number (not DB ID)
    table_number = getattr(table, "table_number", None) or f"#{getattr(call, 'table_id', '?')}"

    from datetime import datetime as _dt
    now_str = _dt.now().strftime("%H:%M")

    text = (
        f"🔔 STOL CHAQIRUVI\n"
        f"{'─' * 28}\n"
        f"🪑 Stol: {table_number}\n"
        f"🕐 Vaqt: {now_str}\n"
        f"ID: #{getattr(call, 'id', '?')}"
    )

    try:
        bot = get_location_bot(location)
        bot.send_message(dispatcher_id, text)
        logger.info(
            "notify_waiter_call: call_id=%s table=%s dispatcher=%s location_id=%s",
            getattr(call, "id", "?"),
            table_number,
            dispatcher_id,
            location.id,
        )
    except ValueError as e:
        logger.warning("notify_waiter_call: %s", e)
    except Exception:
        logger.exception(
            "notify_waiter_call: ошибка отправки call_id=%s",
            getattr(call, "id", "?"),
        )
