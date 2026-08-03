"""
AI Router for Taomly
Endpoints для AI-функций. Все провайдер-независимые.
Если AI_ENABLED=false — возвращают feature_not_available без ошибок.

Защита от злоупотреблений:
  - max_length на всех строковых полях (предотвращает дорогие промпты)
  - max_items=50 / min_items=1 на списках
  - @limiter.limit("10/minute") на каждом эндпоинте
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ai_service import (
    generate_dish_description,
    generate_menu_seo,
    suggest_dish_tags,
    translate_menu,
)
from auth import get_current_restaurant_admin
from limiter import limiter

router = APIRouter(prefix="/api/ai", tags=["AI"])

# ── Константа: максимум символов во всех строковых полях запроса ──────
# Превентивная защита от дорогих промптов.
# Соответствует MAX_PROMPT_CHARS в ai_service.py — менять синхронно.
_MAX_FIELD_CHARS_SHORT = 200    # dish_name, restaurant_name, language
_MAX_FIELD_CHARS_LONG  = 1000   # ingredients, description, menu_summary
_MAX_TRANSLATE_ITEMS   = 50     # позиций за один запрос


# ── Схемы ─────────────────────────────────────────────────────────────

class DishDescriptionRequest(BaseModel):
    dish_name:   str            = Field(..., min_length=1, max_length=_MAX_FIELD_CHARS_SHORT)
    ingredients: Optional[str]  = Field("",  max_length=_MAX_FIELD_CHARS_LONG)
    language:    Optional[str]  = Field("en", max_length=50)


class TranslateMenuItem(BaseModel):
    """Одна позиция меню для перевода."""
    name:        str            = Field(..., min_length=1, max_length=_MAX_FIELD_CHARS_SHORT)
    description: Optional[str]  = Field("", max_length=_MAX_FIELD_CHARS_LONG)
    price:       Optional[float] = None


class TranslateMenuRequest(BaseModel):
    items:           list[TranslateMenuItem] = Field(..., min_length=1, max_length=_MAX_TRANSLATE_ITEMS)
    target_language: Optional[str]           = Field("uz", max_length=50)


class SuggestTagsRequest(BaseModel):
    dish_name:   str            = Field(..., min_length=1, max_length=_MAX_FIELD_CHARS_SHORT)
    description: Optional[str]  = Field("",  max_length=_MAX_FIELD_CHARS_LONG)
    ingredients: Optional[str]  = Field("",  max_length=_MAX_FIELD_CHARS_LONG)


class MenuSeoRequest(BaseModel):
    restaurant_name: str           = Field(..., min_length=1, max_length=_MAX_FIELD_CHARS_SHORT)
    menu_summary:    Optional[str] = Field("", max_length=_MAX_FIELD_CHARS_LONG)
    language:        Optional[str] = Field("en", max_length=50)


# ── Эндпоинты ─────────────────────────────────────────────────────────

@router.post("/generate-description")
@limiter.limit("10/minute")
async def api_generate_description(
    request: Request,
    body: DishDescriptionRequest,
    current_restaurant=Depends(get_current_restaurant_admin),
):
    """Генерация описания блюда для ресторана."""
    return await generate_dish_description(
        dish_name=body.dish_name,
        ingredients=body.ingredients,
        language=body.language,
    )


@router.post("/translate-menu")
@limiter.limit("10/minute")
async def api_translate_menu(
    request: Request,
    body: TranslateMenuRequest,
    current_restaurant=Depends(get_current_restaurant_admin),
):
    """Перевод позиций меню на целевой язык (ru/en/uz)."""
    return await translate_menu(
        items=[item.model_dump() for item in body.items],
        target_language=body.target_language,
    )


@router.post("/suggest-tags")
@limiter.limit("10/minute")
async def api_suggest_tags(
    request: Request,
    body: SuggestTagsRequest,
    current_restaurant=Depends(get_current_restaurant_admin),
):
    """Генерация тегов блюда: острое, вегетарианское, халяль и т.д."""
    return await suggest_dish_tags(
        dish_name=body.dish_name,
        description=body.description,
        ingredients=body.ingredients,
    )


@router.post("/generate-seo")
@limiter.limit("10/minute")
async def api_generate_seo(
    request: Request,
    body: MenuSeoRequest,
    current_restaurant=Depends(get_current_restaurant_admin),
):
    """Генерация SEO-описания меню ресторана."""
    return await generate_menu_seo(
        restaurant_name=body.restaurant_name,
        menu_summary=body.menu_summary,
        language=body.language,
    )
