import os
import logging
import telebot

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# ID сотрудников "Чинор" (пока оба — ты)
DISPATCHER_ID = 331294063   # доставка и самовывоз
MANAGER_ID    = 331294063   # заказы в зале


def notify_new_order(order, items):
    """Уведомление персоналу о новом заказе."""
    
    order_type_labels = {
        "delivery":  "🛵 Yetkazib berish",
        "takeaway":  "🥡 Olib ketish",
        "dine_in":   "🍽️ Zal (stol)",
    }
    type_label = order_type_labels.get(order.order_type, order.order_type)

    # Список блюд
    items_text = ""
    for item in items:
        items_text += f"  • {item.name} × {item.quantity} — {int(item.price * item.quantity):,} so'm\n"

    # Адрес или стол
    location_text = ""
    if order.order_type == "delivery" and order.address:
        location_text = f"📍 Manzil: {order.address}\n"
    elif order.order_type == "dine_in" and order.table_id:
        location_text = f"🪑 Stol: #{order.table_id}\n"

    comment_text = f"💬 Izoh: {order.comment}\n" if order.comment else ""

    message = (
        f"🔔 YANGI BUYURTMA #{order.id}\n"
        f"{'─' * 28}\n"
        f"{type_label}\n"
        f"👤 {order.client_name}\n"
        f"📞 {order.client_phone}\n"
        f"{location_text}"
        f"{comment_text}"
        f"{'─' * 28}\n"
        f"{items_text}"
        f"{'─' * 28}\n"
        f"💰 Jami: {int(order.total_amount):,} so'm"
    )

    # Кому отправить
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
