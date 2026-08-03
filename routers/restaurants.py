"""
routers/restaurants.py — Taomly Platform

Изменения относительно v1:
  - Унифицированы сообщения об ошибках на русский язык
  - Добавлен статус HTTP_404_NOT_FOUND через именованные константы
  - get_restaurant_by_slug: убраны недоступные продукты из публичного ответа
    (is_available фильтр был, оставлен без изменений)
  - Структура ответа сохранена — фронтенд не сломается

Изменения v6 (Badge Patch C-5, C-6):
  - Добавлены badge-поля в публичный ответ продуктов:
    is_bestseller, is_new, is_spicy, is_chef_choice, is_popular
  - Фронтенд теперь получает реальные данные из БД вместо #hashtag парсинга

Изменения v7 (Bugfix):
  - is_popular: исправлен баг — возвращалось p.is_bestseller вместо p.is_popular.
    Поле is_popular — отдельная колонка в БД (миграция 0003), управляется
    из admin.html независимо от is_bestseller. Секция "Популярное" в Mini App
    теперь корректно отражает выбор ресторана.

Изменения v8 (Settings Endpoint):
  - Добавлен GET /api/restaurants/me/settings — получить настройки ресторана
  - Добавлен PATCH /api/restaurants/me/settings — сохранить настройки ресторана
    (working_hours, delivery_fee, min_order_amount)
  - Оба endpoint требуют роль restaurant_admin
  - Публичный GET /{slug} теперь возвращает working_hours, delivery_fee,
    min_order_amount для отображения клиенту
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from typing import Optional

from auth import get_current_restaurant_admin
from database import get_db
from models import Category, Restaurant, RestaurantTable
from schemas import (
    CategoryPublicResponse,
    ProductPublicResponse,
    RestaurantPublicResponse,
    RestaurantSettingsResponse,
    RestaurantSettingsUpdateResponse,
    TableResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/restaurants", tags=["restaurants"])


# ──────────────────────────────────────────
# Pydantic схема для настроек
# ──────────────────────────────────────────
class RestaurantSettingsUpdate(BaseModel):
    working_hours: Optional[str] = None
    delivery_fee: Optional[int] = None
    min_order_amount: Optional[int] = None


# ──────────────────────────────────────────
# GET /me/settings — настройки ресторана (только для restaurant_admin)
# ──────────────────────────────────────────
@router.get("/me/settings", response_model=RestaurantSettingsResponse)
def get_restaurant_settings(
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
):
    """
    Возвращает текущие настройки ресторана.
    Требует авторизацию: restaurant_admin.
    """
    return RestaurantSettingsResponse(
        working_hours=restaurant.working_hours or "",
        delivery_fee=restaurant.delivery_fee or 0,
        min_order_amount=restaurant.min_order_amount or 0,
    )


# ──────────────────────────────────────────
# PATCH /me/settings — сохранить настройки (только для restaurant_admin)
# ──────────────────────────────────────────
@router.patch("/me/settings", response_model=RestaurantSettingsUpdateResponse)
def update_restaurant_settings(
    data: RestaurantSettingsUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Сохраняет настройки ресторана: рабочие часы, стоимость доставки,
    минимальная сумма заказа.
    Требует авторизацию: restaurant_admin.
    """
    if data.working_hours is not None:
        restaurant.working_hours = data.working_hours.strip()
    if data.delivery_fee is not None:
        if data.delivery_fee < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Стоимость доставки не может быть отрицательной",
            )
        restaurant.delivery_fee = data.delivery_fee
    if data.min_order_amount is not None:
        if data.min_order_amount < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Минимальная сумма не может быть отрицательной",
            )
        restaurant.min_order_amount = data.min_order_amount

    db.commit()
    db.refresh(restaurant)

    logger.info(
        "Restaurant settings updated: slug=%s working_hours=%s "
        "delivery_fee=%s min_order_amount=%s",
        restaurant.slug,
        restaurant.working_hours,
        restaurant.delivery_fee,
        restaurant.min_order_amount,
    )

    return RestaurantSettingsUpdateResponse(
        ok=True,
        working_hours=restaurant.working_hours or "",
        delivery_fee=restaurant.delivery_fee or 0,
        min_order_amount=restaurant.min_order_amount or 0,
    )


# ──────────────────────────────────────────
# GET /{slug} — публичная информация о ресторане
# ──────────────────────────────────────────
@router.get("/{slug}", response_model=RestaurantPublicResponse)
def get_restaurant_by_slug(slug: str, db: Session = Depends(get_db)):
    """
    Возвращает публичную информацию о ресторане по slug.

    Используется фронтендом при загрузке Mini App:
      1. Получает branding (цвета, лого, welcome_text)
      2. Получает restaurant.id для заголовка X-Restaurant-Id
      3. Получает меню (только доступные продукты) с badge-полями
      4. Получает настройки доставки и рабочие часы

    Авторизация не требуется — публичный эндпоинт.
    telegram_bot_token_encrypted НЕ включается в ответ — защита токена.
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.slug == slug.lower().strip(),
        Restaurant.is_active == True,
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )

    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant.id)
        .options(joinedload(Category.products))
        .order_by(Category.sort_order)
        .all()
    )

    return {
        "id": restaurant.id,
        "name": restaurant.name,
        "slug": restaurant.slug,
        "description": restaurant.description,
        "phone": restaurant.phone,
        "address": restaurant.address,
        "is_waiter_call_enabled": restaurant.is_waiter_call_enabled,
        # White Label branding
        "logo_url": restaurant.logo_url,
        "primary_color": restaurant.primary_color,
        "secondary_color": restaurant.secondary_color,
        "accent_color": restaurant.accent_color,
        "welcome_text": restaurant.welcome_text,
        # Настройки доставки и рабочие часы (для клиента)
        "working_hours": restaurant.working_hours or "",
        "delivery_fee": restaurant.delivery_fee or 0,
        "min_order_amount": restaurant.min_order_amount or 0,
        # telegram_bot_token_encrypted намеренно не включён
        "categories": [
            {
                "id": cat.id,
                "name": cat.name,
                "sort_order": cat.sort_order,
                "products": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "price": p.price,
                        "photo_url": p.photo_url,
                        "is_available": p.is_available,
                        "sort_order": p.sort_order,
                        "is_bestseller": p.is_bestseller,
                        "is_new": p.is_new,
                        "is_spicy": p.is_spicy,
                        "is_chef_choice": p.is_chef_choice,
                        "is_popular": p.is_popular,
                    }
                    for p in sorted(cat.products, key=lambda x: x.sort_order)
                    if p.is_available
                ],
            }
            for cat in categories
            if any(p.is_available for p in cat.products)
        ],
    }


# ──────────────────────────────────────────
# GET /{slug}/table/{table_number} — получить стол по номеру
# ──────────────────────────────────────────
@router.get("/{slug}/table/{table_number}", response_model=TableResponse)
def get_table_by_number(slug: str, table_number: str, db: Session = Depends(get_db)):
    """
    Возвращает данные стола по slug ресторана и номеру стола.

    Используется при сканировании QR-кода:
      QR → /restaurants/{slug}/table/{table_number}
      → фронтенд получает restaurant_id и table_id
      → кладёт в X-Restaurant-Id и передаёт в заказ

    Авторизация не требуется — публичный эндпоинт.
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.slug == slug.lower().strip(),
        Restaurant.is_active == True,
    ).first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )

    table = db.query(RestaurantTable).filter(
        RestaurantTable.restaurant_id == restaurant.id,
        RestaurantTable.table_number == table_number,
    ).first()
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Стол не найден",
        )

    return {
        "restaurant_id": restaurant.id,
        "restaurant_name": restaurant.name,
        "slug": restaurant.slug,
        "table_id": table.id,
        "table_number": table.table_number,
    }
