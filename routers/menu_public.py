"""
routers/menu_public.py — Taomly Platform

Public и admin READ-ONLY endpoints для меню.

Endpoints:
  GET /{restaurant_id}      — публичное меню для клиентов (без авторизации)
  GET /{restaurant_id}/all  — полное меню для admin (JWT required)

Helpers:
  _get_active_restaurant     — загрузка активного ресторана (только для публичного GET)
  _resolve_lang              — Phase 4: разрешение языка меню
  _localized_name            — Phase 4: локализованное название
  _localized_desc            — Phase 4: локализованное описание
  _apply_lang_to_menu        — Phase 4: применение локализации к списку категорий

Извлечено из routers/menu.py (R-1 модуляризация).
"""

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from auth import get_current_restaurant_admin
from database import get_db
from models import (
    Category,
    Location,
    ModifierGroup,
    Product,
    Restaurant,
    MENU_LANGUAGES,
)
from schemas import (
    CategoryPublicResponse,
    CategoryResponseWithTranslations,
)
from utils import is_within_schedule

logger = logging.getLogger(__name__)

router = APIRouter()


# ──────────────────────────────────────────
# ХЕЛПЕР — получить активный ресторан или 404
# ──────────────────────────────────────────
def _get_active_restaurant(restaurant_id: int, db: Session) -> Restaurant:
    """
    Загружает активный ресторан по ID.
    Используется в публичных эндпоинтах (без JWT).
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.is_active == True,
    ).first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )
    return restaurant


# ──────────────────────────────────────────
# PHASE 4: LANGUAGE HELPERS
# ──────────────────────────────────────────

def _resolve_lang(
    query_lang: Optional[str],
    active_location: Optional[Location],
) -> str:
    """
    Детерминированное разрешение языка для публичного меню.

    Приоритет:
      1. Явный ?lang= из query param (уже валидирован FastAPI как Literal)
      2. Location.language первой активной Location
      3. "uz" — абсолютный fallback

    Timezone-переменная в языковую логику не входит.
    """
    if query_lang:
        return query_lang
    if active_location and active_location.language in MENU_LANGUAGES:
        return active_location.language
    return "uz"


def _localized_name(translations: list, lang: str, base_name: str) -> str:
    """Возвращает локализованное name или base_name как fallback."""
    for t in translations:
        if t.language == lang:
            return t.name
    return base_name


def _localized_desc(translations: list, lang: str, base_desc) -> Optional[str]:
    """Возвращает локализованное description или base_desc как fallback."""
    for t in translations:
        if t.language == lang:
            return t.description
    return base_desc


def _apply_lang_to_menu(categories: list, lang: str) -> None:
    """
    Применяет локализацию in-place к списку категорий меню.
    Используется в обоих публичных эндпоинтах.
    Translations уже загружены через lazy="selectin".
    """
    for c in categories:
        c.name = _localized_name(c.translations, lang, c.name)
        for p in (c.products or []):
            p.description = _localized_desc(p.translations, lang, p.description)
            p.name = _localized_name(p.translations, lang, p.name)
            for v in (p.variants or []):
                v.name = _localized_name(v.translations, lang, v.name)
            for g in (p.modifier_groups or []):
                g.name = _localized_name(g.translations, lang, g.name)
                for o in (g.options or []):
                    o.name = _localized_name(o.translations, lang, o.name)


# ──────────────────────────────────────────
# GET /{restaurant_id} — публичное меню (клиент)
# ──────────────────────────────────────────
@router.get("/{restaurant_id}", response_model=List[CategoryPublicResponse])
def get_menu(
    restaurant_id: int,
    lang: Optional[Literal["uz", "ru", "en"]] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Возвращает публичное меню ресторана — только доступные продукты.
    Авторизация не требуется (публичный эндпоинт для клиентов).
    Пустые категории (без доступных продуктов) не возвращаются.

    Phase 3: schedule enforcement.
    Phase 4: ?lang=uz/ru/en — локализация. Дефолт: Location.language → "uz".
    - Timezone: Location.timezone первой активной Location ресторана.
    - Если Location не найдена: продукты без расписания отображаются,
      продукты с расписанием — fail closed (не отображаются).
    """
    _get_active_restaurant(restaurant_id, db)

    # Phase 3 + Phase 4: Location нужна для timezone (Phase 3) и language (Phase 4).
    active_location = (
        db.query(Location)
        .filter(
            Location.restaurant_id == restaurant_id,
            Location.is_active == True,
        )
        .order_by(Location.id)
        .first()
    )

    if active_location is None:
        logger.warning(
            "No active Location found for restaurant_id=%s — scheduled products treated as unavailable",
            restaurant_id,
        )
        tz_str = None
    else:
        tz_str = active_location.timezone

    # Phase 4: resolve language. Timezone не участвует.
    resolved_lang = _resolve_lang(lang, active_location)

    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant_id)
        .options(
            joinedload(Category.products)
            .joinedload(Product.variants)
        )
        .options(
            joinedload(Category.products)
            .joinedload(Product.modifier_groups)
            .joinedload(ModifierGroup.options)
        )
        .order_by(Category.sort_order)
        .all()
    )
    # Phase 4: translations загружены автоматически через lazy="selectin" в models.

    for c in categories:
        available_products = []
        for p in (c.products or []):
            if not p.is_available:
                continue
            if p.available_from is not None or p.available_until is not None:
                if tz_str is None:
                    continue
                if not is_within_schedule(p.available_from, p.available_until, tz_str):
                    continue
            available_products.append(p)

        for p in available_products:
            active_variants = [v for v in (p.variants or []) if v.is_active]
            p.variants = sorted(active_variants, key=lambda v: (v.sort_order, v.id))
            active_groups = [g for g in (p.modifier_groups or []) if g.is_active]
            for g in active_groups:
                g.options = [o for o in (g.options or []) if o.is_active]
            p.modifier_groups = active_groups

        c.products = sorted(available_products, key=lambda p: p.sort_order)

    result = [c for c in categories if c.products]

    # Phase 4: применяем локализацию после фильтрации.
    _apply_lang_to_menu(result, resolved_lang)

    return result


# ──────────────────────────────────────────
# GET /{restaurant_id}/all — полное меню (админка)
# ──────────────────────────────────────────
@router.get("/{restaurant_id}/all", response_model=List[CategoryResponseWithTranslations])
def get_menu_all(
    restaurant_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает полное меню ресторана включая недоступные продукты.
    Phase 4: включает поле translations для каждой сущности.

    Tenant-изоляция: restaurant_id из URL проверяется против токена JWT.
    """
    if restaurant.id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к меню этого ресторана",
        )

    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant_id)
        .options(
            joinedload(Category.products)
            .joinedload(Product.modifier_groups)
            .joinedload(ModifierGroup.options)
        )
        .order_by(Category.sort_order)
        .all()
    )
    # Phase 4: translations загружены через lazy="selectin".

    for c in categories:
        c.products = sorted(c.products or [], key=lambda p: p.sort_order)

    return categories
