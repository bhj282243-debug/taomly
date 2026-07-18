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
  - is_popular = is_bestseller (алиас для обратной совместимости с index.html)
  - Фронтенд теперь получает реальные данные из БД вместо #hashtag парсинга
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Category, Restaurant, RestaurantTable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/restaurants", tags=["restaurants"])


# ──────────────────────────────────────────
# GET /{slug} — публичная информация о ресторане
# ──────────────────────────────────────────
@router.get("/{slug}")
def get_restaurant_by_slug(slug: str, db: Session = Depends(get_db)):
    """
    Возвращает публичную информацию о ресторане по slug.

    Используется фронтендом при загрузке Mini App:
      1. Получает branding (цвета, лого, welcome_text)
      2. Получает restaurant.id для заголовка X-Restaurant-Id
      3. Получает меню (только доступные продукты) с badge-полями

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
                        # Badge-поля из БД (C-6)
                        # Раньше бейджи кодировались как #hashtag в description —
                        # теперь это отдельные булевые колонки.
                        "is_bestseller": p.is_bestseller,
                        "is_new": p.is_new,
                        "is_spicy": p.is_spicy,
                        "is_chef_choice": p.is_chef_choice,
                        # is_popular — алиас is_bestseller для index.html (C-5)
                        "is_popular": p.is_bestseller,
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
@router.get("/{slug}/table/{table_number}")
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
