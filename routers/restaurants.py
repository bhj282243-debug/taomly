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
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
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
    TableCreateRequest,
    TableCreateResponse,
    TableItem,
    TablesListResponse,
    TableResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/restaurants", tags=["restaurants"])


# ──────────────────────────────────────────
# Pydantic схема для настроек
# ──────────────────────────────────────────
_SAFE_TZ_RE_SETTINGS = __import__("re").compile(r"^[A-Za-z_]+/[A-Za-z_/]+$|^UTC$")


_SUPPORTED_CURRENCIES = {"UZS", "KZT", "RUB", "USD", "TRY", "AED"}
_SUPPORTED_LANGUAGES  = {"uz", "ru", "en"}


class RestaurantSettingsUpdate(BaseModel):
    working_hours:    Optional[str] = Field(None, max_length=50)
    # Foundation Task 11.3: ge=0 + le=10_000_000 (10 млн сум).
    # Без верхней границы значение > 2_147_483_647 вызывает PostgreSQL
    # IntegerOverflow → HTTP 500 вместо корректного 422.
    delivery_fee:     Optional[int] = Field(None, ge=0, le=10_000_000)
    min_order_amount: Optional[int] = Field(None, ge=0, le=10_000_000)
    timezone:         Optional[str] = None
    currency:         Optional[str] = None
    language:         Optional[str] = None


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
        timezone=getattr(restaurant, "timezone", None) or "Asia/Tashkent",
        currency=getattr(restaurant, "currency", None) or "UZS",
        language=getattr(restaurant, "language", None) or "uz",
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
    if data.timezone is not None:
        tz = data.timezone.strip()
        if not _SAFE_TZ_RE_SETTINGS.match(tz):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Невалидный timezone. Пример: Asia/Tashkent, Asia/Almaty, UTC",
            )
        restaurant.timezone = tz

    if data.currency is not None:
        cur = data.currency.strip().upper()
        if cur not in _SUPPORTED_CURRENCIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Неподдерживаемая валюта '{cur}'. "
                    f"Допустимые: {', '.join(sorted(_SUPPORTED_CURRENCIES))}"
                ),
            )
        restaurant.currency = cur

    if data.language is not None:
        lang = data.language.strip().lower()
        if lang not in _SUPPORTED_LANGUAGES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Неподдерживаемый язык '{lang}'. "
                    f"Допустимые: {', '.join(sorted(_SUPPORTED_LANGUAGES))}"
                ),
            )
        restaurant.language = lang

    try:
        db.commit()
        db.refresh(restaurant)
    except Exception:
        db.rollback()
        logger.exception(
            "Ошибка при сохранении настроек ресторана: slug=%s",
            restaurant.slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при сохранении настроек ресторана",
        )

    logger.info(
        "Restaurant settings updated: slug=%s working_hours=%s "
        "delivery_fee=%s min_order_amount=%s timezone=%s currency=%s language=%s",
        restaurant.slug,
        restaurant.working_hours,
        restaurant.delivery_fee,
        restaurant.min_order_amount,
        restaurant.timezone,
        restaurant.currency,
        getattr(restaurant, "language", "uz"),
    )

    return RestaurantSettingsUpdateResponse(
        ok=True,
        working_hours=restaurant.working_hours or "",
        delivery_fee=restaurant.delivery_fee or 0,
        min_order_amount=restaurant.min_order_amount or 0,
        timezone=getattr(restaurant, "timezone", None) or "Asia/Tashkent",
        currency=getattr(restaurant, "currency", None) or "UZS",
        language=getattr(restaurant, "language", None) or "uz",
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
        # Валюта ресторана: UZS, KZT, RUB, USD, TRY, AED
        "currency": getattr(restaurant, "currency", None) or "UZS",
        # Язык клиентского UI: uz, ru, en
        "language": getattr(restaurant, "language", None) or "uz",
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
# ──────────────────────────────────────────
# GET  /me/tables  — список столов ресторана
# POST /me/tables  — создать стол
# DELETE /me/tables/{table_id} — удалить стол
# ──────────────────────────────────────────

@router.get("/me/tables", response_model=TablesListResponse)
def list_tables(
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает все столы ресторана.
    Требует авторизацию: restaurant_admin.
    """
    tables = (
        db.query(RestaurantTable)
        .filter(RestaurantTable.restaurant_id == restaurant.id)
        .order_by(RestaurantTable.table_number)
        .all()
    )
    items = [
        TableItem(
            id=t.id,
            table_number=t.table_number,
            created_at=t.created_at.isoformat(),
        )
        for t in tables
    ]
    return TablesListResponse(tables=items, total=len(items))


@router.post("/me/tables", response_model=TableCreateResponse, status_code=201)
def create_table(
    data: TableCreateRequest,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Создаёт новый стол для ресторана.
    Номер стола должен быть уникальным в рамках ресторана.
    Требует авторизацию: restaurant_admin.
    """
    existing = db.query(RestaurantTable).filter(
        RestaurantTable.restaurant_id == restaurant.id,
        RestaurantTable.table_number == data.table_number,
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Стол '{data.table_number}' уже существует",
        )

    table = RestaurantTable(
        restaurant_id=restaurant.id,
        table_number=data.table_number,
    )
    db.add(table)
    try:
        db.commit()
    except IntegrityError:
        # Защита от TOCTOU: параллельный запрос мог создать тот же
        # table_number между проверкой выше и этим commit(). Уникальный
        # constraint в БД — последний рубеж; конвертируем в чистый 409
        # вместо утечки сырой ошибки PostgreSQL через 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Стол '{data.table_number}' уже существует",
        )
    db.refresh(table)

    logger.info(
        "Table created: restaurant_id=%s table_number=%s id=%s",
        restaurant.id,
        table.table_number,
        table.id,
    )
    return TableCreateResponse(ok=True, id=table.id, table_number=table.table_number)


@router.delete("/me/tables/{table_id}", status_code=204)
def delete_table(
    table_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Удаляет стол по ID.
    Стол должен принадлежать ресторану текущего admin.
    Требует авторизацию: restaurant_admin.
    """
    table = db.query(RestaurantTable).filter(
        RestaurantTable.id == table_id,
        RestaurantTable.restaurant_id == restaurant.id,
    ).first()
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Стол не найден",
        )
    db.delete(table)
    db.commit()
    logger.info(
        "Table deleted: restaurant_id=%s table_id=%s table_number=%s",
        restaurant.id,
        table_id,
        table.table_number,
    )


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
