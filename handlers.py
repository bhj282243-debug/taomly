"""
handlers.py — Taomly Platform

Изменения v2:
  - Добавлен BOT_CACHE: dict — один TeleBot на ресторан, создаётся один раз.
    Устраняет создание сотен объектов при нагрузке.
  - decrypt_token вызывается только при первом создании бота для ресторана.
  - notify_new_order: улучшен лог — добавлен restaurant.name для читаемости.
  - notify_client_accepted: принимает restaurant вторым аргументом (Multi-Tenant).
"""

import logging
import os
from typing import Dict

import telebot

from auth import decrypt_token

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# ПЛАТФОРМЕННЫЙ БОТ (Agency / onboarding)
# ──────────────────────────────────────────
_PLATFORM_BOT_TOKEN = os.getenv("BOT_TOKEN")
platform_bot = telebot.TeleBot(_PLATFORM_BOT_TOKEN) if _PLATFORM_BOT_TOKEN else None

# ──────────────────────────────────────────
# КЭШ БОТОВ — один объект TeleBot на ресторан
# Ключ: restaurant.id → TeleBot
# При текущем масштабе (Render Free, один воркер) dict достаточен.
# ──────────────────────────────────────────
_BOT_CACHE: Dict[int, telebot.TeleBot] = {}


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
                web_app=telebot.types.WebAppInfo(url="https://taomly.onrender.com/app"),
            )
        )

        inline_markup = telebot.types.InlineKeyboardMarkup()
        inline_markup.add(
            telebot.types.InlineKeyboardButton(
                text="🍽️  Menyuni ochish  →",
                web_app=telebot.types.WebAppInfo(url="https://taomly.onrender.com/app"),
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
                web_app=telebot.types.WebAppInfo(url="https://taomly.onrender.com/app"),
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
def notify_new_order(order, items, restaurant) -> None:
    """
    Отправляет уведомление диспетчеру ресторана о новом заказе.

    Multi-Tenant: dispatcher_id и бот берутся из объекта restaurant.
    Вызывается через BackgroundTasks — не блокирует HTTP-ответ.
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

    items_text = "".join(
        f"  • {item.name} × {item.quantity} — {item.price * item.quantity:,} so'm\n"
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
        f"💰 Jami: {int(order.total_amount):,} so'm"
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
def notify_client_accepted(order, restaurant) -> None:
    """
    Отправляет клиенту уведомление что заказ принят рестораном.

    Multi-Tenant: использует бот конкретного ресторана — клиент получает
    сообщение от того же бота, через которого делал заказ.
    Вызывается через BackgroundTasks — не блокирует HTTP-ответ.
    """
    if not order.client_telegram_id:
        logger.warning(
            "notify_client_accepted: заказ #%s не имеет client_telegram_id",
            order.id,
        )
        return

    order_type_labels = {
        "delivery": "yetkazib beriladi",
        "takeaway": "olib ketishingiz mumkin",
        "dine_in":  "tayyorlanmoqda",
    }
    action = order_type_labels.get(order.order_type, "tayyorlanmoqda")

    text = (
        f"✅ Buyurtmangiz qabul qilindi!\n"
        f"{'─' * 28}\n"
        f"Buyurtma #{order.id} — {int(order.total_amount):,} so'm\n"
        f"Tez orada {action} 🙏"
    )

    try:
        bot = get_restaurant_bot(restaurant)
        bot.send_message(order.client_telegram_id, text)
        logger.info(
            "Уведомление клиенту %s о принятии заказа #%s (ресторан «%s» id=%s)",
            order.client_telegram_id,
            order.id,
            restaurant.name,
            restaurant.id,
        )
    except ValueError as e:
        logger.warning("notify_client_accepted: %s", e)
    except Exception:
        logger.exception(
            "Ошибка уведомления клиента: заказ #%s клиент %s ресторан «%s»",
            order.id,
            order.client_telegram_id,
            restaurant.name,
        )
