"""
AI Router for Taomly
Endpoints для AI-функций. Все провайдер-независимые.
Если AI_ENABLED=false — возвращают feature_not_available без ошибок.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from auth import get_current_restaurant_admin
from ai_service import (
    generate_dish_description,
    translate_menu,
    suggest_dish_tags,
    generate_menu_seo
)

router = APIRouter(prefix="/api/ai", tags=["AI"])


# ── Schemas ──

class DishDescriptionRequest(BaseModel):
    dish_name: str
    ingredients: Optional[str] = ""
    language: Optional[str] = "en"


class TranslateMenuRequest(BaseModel):
    items: list[dict]
    target_language: Optional[str] = "uz"


class SuggestTagsRequest(BaseModel):
    dish_name: str
    description: Optional[str] = ""
    ingredients: Optional[str] = ""


class MenuSeoRequest(BaseModel):
    restaurant_name: str
    menu_summary: Optional[str] = ""
    language: Optional[str] = "en"


# ── Endpoints ──

@router.post("/generate-description")
async def api_generate_description(
    body: DishDescriptionRequest,
    current_restaurant=Depends(get_current_restaurant_admin)
):
    """Генерация описания блюда для ресторана."""
    return await generate_dish_description(
        dish_name=body.dish_name,
        ingredients=body.ingredients,
        language=body.language
    )


@router.post("/translate-menu")
async def api_translate_menu(
    body: TranslateMenuRequest,
    current_restaurant=Depends(get_current_restaurant_admin)
):
    """Перевод позиций меню на целевой язык (ru/en/uz)."""
    return await translate_menu(
        items=body.items,
        target_language=body.target_language
    )


@router.post("/suggest-tags")
async def api_suggest_tags(
    body: SuggestTagsRequest,
    current_restaurant=Depends(get_current_restaurant_admin)
):
    """Генерация тегов блюда: острое, вегетарианское, халяль и т.д."""
    return await suggest_dish_tags(
        dish_name=body.dish_name,
        description=body.description,
        ingredients=body.ingredients
    )


@router.post("/generate-seo")
async def api_generate_seo(
    body: MenuSeoRequest,
    current_restaurant=Depends(get_current_restaurant_admin)
):
    """Генерация SEO-описания меню ресторана."""
    return await generate_menu_seo(
        restaurant_name=body.restaurant_name,
        menu_summary=body.menu_summary,
        language=body.language
    )
