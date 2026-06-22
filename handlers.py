import os
import logging
import telebot

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# ID сотрудников "Чинор"
DISPATCHER_ID = 331294063
MANAGER_ID    = 331294063


@bot.message_handler(commands=['start'])
def handle_start(message):
    """Приветствие с кнопкой открытия меню."""

    # Постоянная кнопка над клавиатурой (всегда видна)
    reply_markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    reply_markup.add(
        telebot.types.KeyboardButton(
            text="🍽️ Menyuni ochish",
            web_app=telebot.types.WebAppInfo(url="https://taomly.onrender.com/app")
        )
    )

    # Inline кнопка в сообщении
    inline_markup = telebot.types.InlineKeyboardMarkup()
    inline_markup.add(
        telebot.types.InlineKeyboardButton(
            text="🍽️ Menyuni ochish",
            web_app=telebot.types.WebAppInfo(url="https://taomly.onrender.com/app")
        )
    )

    name = message.from_user.first_name or "mehmon"

    bot.send_message(
        message.chat.id,
        f"👋 Assalomu alaykum, *{name}*!\n\n"
        f"🍽️ *Chinar Restaurant*ga xush kelibsiz!\n\n"
        f"Bizning mazali taomlarimizdan buyurtma bering — tez va qulay 🚀\n\n"
        f"👇 Quyidagi tugmani bosing:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    bot.send_message(
        message.chat.id,
        "⬇️",
        reply_markup=inline_markup
    )


@bot.message_handler(func=lambda message: message.text == "🍽️ Menyuni ochish")
def handle_menu_button(message):
    """Обработка нажатия на постоянную кнопку меню."""
    inline_markup = telebot.types.InlineKeyboardMarkup()
    inline_markup.add(
        telebot.types.InlineKeyboardButton(
            text="🍽️ Menyuni ochish",
            web_app=telebot.types.WebAppInfo(url="https://taomly.onrender.com/app")
        )
    )
    bot.send_message(
        message.chat.id,
        "🍽️ Menyuni ochish uchun quyidagi tugmani bosing:",
        reply_markup=inline_markup
    )


def notify_new_order(order, items):
    """Уведомление персоналу о новом заказе."""

    order_type_labels = {
        "delivery":  "🛵 Yetkazib berish",
        "takeaway":  "🥡 Olib ketish",
        "dine_in":   "🍽️ Zal (stol)",
    }
    type_label = order_type_labels.get(order.order_type, order.order_type)

    items_text = ""
    for item in items:
        items_text += f"  • {item.name} × {item.quantity} — {int(item.price * item.quantity):,} so'm\n"

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

    message = (
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

    if order.order_type == "dine_in":
        target_id = MANAGER_ID
    else:
        target_id = DISPATCHER_ID

    try:
        bot.send_message(target_id, message)
        logger.info(f"Уведомление о заказе #{order.id} отправлено → {target_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления персоналу: {e}")


def notify_client_accepted(order):
    """Уведомление клиенту — заказ принят."""

    if not order.client_telegram_id:
        return

    order_type_labels = {
        "delivery":  "yetkazib beriladi",
        "takeaway":  "olib ketishingiz mumkin",
        "dine_in":   "tayyorlanmoqda",
    }
    action = order_type_labels.get(order.order_type, "tayyorlanmoqda")

    message = (
        f"✅ Buyurtmangiz qabul qilindi!\n"
        f"{'─' * 28}\n"
        f"Buyurtma #{order.id} — {int(order.total_amount):,} so'm\n"
        f"Tez orada {action} 🙏"
    )

    try:
        bot.send_message(order.client_telegram_id, message)
        logger.info(f"Уведомление клиенту {order.client_telegram_id} о заказе #{order.id}")
    except Exception as e:
        logger.error(f"Ошибка уведомления клиента: {e}")
