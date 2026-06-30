"""
telegram_service.py — Taomly Platform

Централизованный сервис для управления Telegram Bot webhook'ами
ресторанов. Используется роутером agency.py при создании,
обновлении токена и деактивации ресторана.

Каждый ресторан имеет собственного Telegram-бота с собственным
webhook-эндпоинтом: /webhook/{slug}. Это позволяет каждому боту
сразу отвечать на /start с кнопкой Mini App именно своего ресторана —
без какой-либо ручной настройки через Postman или Telegram API.

Архитектурное решение — webhook по slug, а не по restaurant_id:
  - slug уже является публичным идентификатором ресторана во всей
    остальной системе (используется в /app?slug=..., в admin login);
  - slug менее предсказуем для перебора, чем последовательный id;
  - упрощает чтение логов и отладку (sentry/render logs показывают
    понятное имя вместо числа).
"""

import logging
from dataclasses import dataclass
from typing import Optional

import telebot

from auth import decrypt_token

logger = logging.getLogger(__name__)


@dataclass
class WebhookResult:
    """Результат попытки настроить webhook для ресторана."""
    ok: bool
    detail: str
    bot_username: Optional[str] = None


def _build_webhook_url(webhook_base_url: str, slug: str) -> str:
    return f"{webhook_base_url.rstrip('/')}/webhook/{slug}"


def verify_bot_token(bot_token: str) -> Optional[str]:
    """
    Проверяет токен бота через Telegram getMe.

    Returns:
        username бота при успехе, None если токен невалиден.
    """
    try:
        bot = telebot.TeleBot(bot_token)
        me = bot.get_me()
        return me.username
    except Exception as exc:
        logger.warning("verify_bot_token: токен невалиден — %s", exc)
        return None


def register_restaurant_webhook(
    bot_token: str,
    slug: str,
    webhook_base_url: Optional[str],
    webhook_secret: str,
    restaurant_name: str = "",
) -> WebhookResult:
    """
    Полный цикл подключения бота ресторана: getMe → deleteWebhook → setWebhook.

    Вызывается автоматически при создании ресторана и при смене токена.
    Никогда не бросает исключения — все ошибки возвращаются в WebhookResult,
    чтобы create_restaurant мог продолжить работу (ресторан создаётся
    в любом случае, даже если бот временно не настроен).
    """
    if not webhook_base_url:
        logger.warning(
            "register_restaurant_webhook: WEBHOOK_URL не задан — "
            "webhook для «%s» (slug=%s) не зарегистрирован", restaurant_name, slug,
        )
        return WebhookResult(ok=False, detail="WEBHOOK_URL не настроен на сервере")

    # 1. getMe — проверяем что токен валиден, прежде чем что-либо делать
    try:
        bot = telebot.TeleBot(bot_token)
        me = bot.get_me()
    except Exception as exc:
        logger.warning(
            "Telegram Bot Token invalid: ресторан «%s» (slug=%s) — %s",
            restaurant_name, slug, exc,
        )
        return WebhookResult(ok=False, detail="Telegram Bot Token invalid")

    logger.info(
        "Bot verified: @%s для ресторана «%s» (slug=%s)",
        me.username, restaurant_name, slug,
    )

    # 2. deleteWebhook — снимаем предыдущий webhook (если был, на старый URL)
    try:
        bot.remove_webhook()
        logger.info("Webhook removed (previous): @%s slug=%s", me.username, slug)
    except Exception:
        logger.exception(
            "Не удалось снять предыдущий webhook для @%s slug=%s — продолжаем",
            me.username, slug,
        )

    # 3. setWebhook — регистрируем новый, на актуальный URL ресторана
    webhook_url = _build_webhook_url(webhook_base_url, slug)
    try:
        bot.set_webhook(url=webhook_url, secret_token=webhook_secret)
    except Exception as exc:
        logger.exception(
            "Telegram API error при setWebhook: @%s slug=%s url=%s",
            me.username, slug, webhook_url,
        )
        return WebhookResult(
            ok=False,
            detail=f"Не удалось зарегистрировать webhook: {exc}",
            bot_username=me.username,
        )

    logger.info(
        "Webhook registered: @%s slug=%s → %s", me.username, slug, webhook_url,
    )
    return WebhookResult(ok=True, detail="Webhook зарегистрирован", bot_username=me.username)


def remove_restaurant_webhook(bot_token: str, slug: str, restaurant_name: str = "") -> None:
    """
    Удаляет webhook бота. Вызывается при деактивации ресторана
    или перед регистрацией нового webhook на смене токена.
    Не бросает исключения — деактивация ресторана не должна падать
    из-за недоступности Telegram API.
    """
    try:
        bot = telebot.TeleBot(bot_token)
        bot.remove_webhook()
        logger.info("Webhook removed: ресторан «%s» (slug=%s)", restaurant_name, slug)
    except Exception:
        logger.exception(
            "Telegram API error при удалении webhook: ресторан «%s» (slug=%s)",
            restaurant_name, slug,
        )


def setup_restaurant_bot_from_encrypted(
    encrypted_token: str,
    slug: str,
    webhook_base_url: Optional[str],
    webhook_secret: str,
    restaurant_name: str = "",
) -> WebhookResult:
    """Удобная обёртка: расшифровывает токен и регистрирует webhook."""
    try:
        bot_token = decrypt_token(encrypted_token)
    except Exception:
        logger.exception(
            "setup_restaurant_bot_from_encrypted: не удалось расшифровать токен "
            "ресторана «%s» (slug=%s)", restaurant_name, slug,
        )
        return WebhookResult(ok=False, detail="Не удалось расшифровать токен бота")

    return register_restaurant_webhook(
        bot_token=bot_token,
        slug=slug,
        webhook_base_url=webhook_base_url,
        webhook_secret=webhook_secret,
        restaurant_name=restaurant_name,
    )
