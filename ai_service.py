"""
AI Service Layer for Taomly
Провайдер-независимый сервисный слой для AI-функций.
Для подключения реального AI — изменить только этот файл.
Провайдеры: OpenRouter, OpenAI, Anthropic, Gemini, локальная модель.
"""

import os
import logging

logger = logging.getLogger(__name__)

# ── Конфигурация из .env ──
AI_ENABLED = os.getenv("AI_ENABLED", "false").lower() == "true"
AI_PROVIDER = os.getenv("AI_PROVIDER", "openrouter")  # openrouter | openai | anthropic | gemini
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "mistralai/mistral-7b-instruct")

FEATURE_NOT_AVAILABLE = {
    "status": "feature_not_available",
    "message": "AI features are disabled. Set AI_ENABLED=true in environment variables.",
    "ai_enabled": False
}


def _check_enabled() -> dict | None:
    """Возвращает заглушку если AI отключён."""
    if not AI_ENABLED:
        return FEATURE_NOT_AVAILABLE
    if not AI_API_KEY:
        logger.warning("AI_ENABLED=true but AI_API_KEY is not set")
        return {
            "status": "feature_not_available",
            "message": "AI_API_KEY is not configured.",
            "ai_enabled": False
        }
    return None


async def generate_dish_description(
    dish_name: str,
    ingredients: str = "",
    language: str = "en"
) -> dict:
    """
    Генерация описания блюда.
    Заглушка — возвращает шаблонный ответ пока AI_ENABLED=false.
    Для активации: установить AI_ENABLED=true и AI_API_KEY в .env
    """
    stub = _check_enabled()
    if stub:
        return stub

    # TODO: вызов реального AI провайдера
    # Пример для OpenRouter:
    # import httpx
    # async with httpx.AsyncClient() as client:
    #     response = await client.post(
    #         "https://openrouter.ai/api/v1/chat/completions",
    #         headers={"Authorization": f"Bearer {AI_API_KEY}"},
    #         json={
    #             "model": AI_MODEL,
    #             "messages": [{"role": "user", "content": prompt}]
    #         }
    #     )

    return {
        "status": "stub",
        "dish_name": dish_name,
        "language": language,
        "description": f"[AI stub] Description for '{dish_name}' will be generated here.",
        "provider": AI_PROVIDER,
        "model": AI_MODEL,
        "ai_enabled": True
    }


async def translate_menu(
    items: list[dict],
    target_language: str = "uz"
) -> dict:
    """
    Перевод меню (RU → UZ/EN или EN → RU/UZ).
    Заглушка — возвращает исходные данные пока AI_ENABLED=false.
    Поддерживаемые языки: ru, en, uz
    """
    stub = _check_enabled()
    if stub:
        return stub

    # TODO: вызов реального AI провайдера для перевода
    return {
        "status": "stub",
        "target_language": target_language,
        "translated_items": items,
        "note": "[AI stub] Translation will be applied here.",
        "provider": AI_PROVIDER,
        "model": AI_MODEL,
        "ai_enabled": True
    }


async def suggest_dish_tags(
    dish_name: str,
    description: str = "",
    ingredients: str = ""
) -> dict:
    """
    Генерация тегов блюда: острое, вегетарианское, популярное, халяль и т.д.
    Заглушка — возвращает пустые теги пока AI_ENABLED=false.
    """
    stub = _check_enabled()
    if stub:
        return stub

    # TODO: вызов реального AI провайдера для генерации тегов
    return {
        "status": "stub",
        "dish_name": dish_name,
        "suggested_tags": [],
        "note": "[AI stub] Tags will be suggested here.",
        "provider": AI_PROVIDER,
        "model": AI_MODEL,
        "ai_enabled": True
    }


async def generate_menu_seo(
    restaurant_name: str,
    menu_summary: str = "",
    language: str = "en"
) -> dict:
    """
    Генерация SEO-описания меню ресторана.
    Заглушка — для будущего использования.
    """
    stub = _check_enabled()
    if stub:
        return stub

    return {
        "status": "stub",
        "restaurant_name": restaurant_name,
        "language": language,
        "seo_title": f"[AI stub] SEO title for {restaurant_name}",
        "seo_description": "[AI stub] SEO description will be generated here.",
        "provider": AI_PROVIDER,
        "model": AI_MODEL,
        "ai_enabled": True
    }
