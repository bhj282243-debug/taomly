"""
routers/restaurants.py — Taomly Platform

Изменения v10 (S1-7: Settings → Location):
  - GET /me/settings: source of truth = первая активная Location ресторана.
  - PATCH /me/settings: пишет в Location; поля restaurant.* обновляются
    синхронно только там, где старый код ещё их читает (working_hours,
    min_order_amount, currency) — backward compat до Migration 0015.
  - GET /{slug}: operational settings (delivery_fee, min_order_amount,
    working_hours, currency, language) берутся из первой активной Location.
  - Telegram credentials (telegram_bot_token_encrypted, telegram_dispatcher_id)
    в S1-7 НЕ переносятся — отдельный шаг после аудита webhook.

Изменения v9 (S1-5: Location CRUD):
  - GET  /api/restaurants/me/locations               — список Location ресторана
  - POST /api/restaurants/me/locations               — создать Location
  - GET  /api/restaurants/me/locations/{location_id} — деталь Location
  - PATCH /api/restaurants/me/locations/{location_id} — обновить Location
  - DELETE /api/restaurants/me/locations/{location_id} — soft-delete (is_active=False)
  - Все endpoints: tenant-изоляция по restaurant_id из JWT.
  - DELETE guard: нельзя деактивировать единственную активную Location (400).
  - Slug uniqueness: IntegrityError → 409.

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
from models import Category, Location, Restaurant, RestaurantTable
from schemas import (
    CategoryPublicResponse,
    LocationCreate,
    LocationListResponse,
    LocationResponse,
    LocationUpdate,
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
# HELPER — получить первую активную Location ресторана
# ──────────────────────────────────────────
def _get_primary_location(db: Session, restaurant_id: int) -> Location:
    """
    Возвращает первую активную Location ресторана (ORDER BY id ASC).

    S1-7: Location — source of truth для operational settings.
    Гарантировано: каждый ресторан имеет ≥1 Location (S1-1 backfill + S1-6 auto-create).

    Raises HTTP 500 если Location не найдена — это баг данных, не ошибка клиента.
    """
    loc = (
        db.query(Location)
        .filter(
            Location.restaurant_id == restaurant_id,
            Location.is_active == True,
        )
        .order_by(Location.id)
        .first()
    )
    if loc is None:
        logger.error(
            "Не найдена активная Location для restaurant_id=%s — нарушен инвариант S1-1",
            restaurant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Активная локация ресторана не найдена. Обратитесь к администратору.",
        )
    return loc


# ──────────────────────────────────────────
# GET /me/settings — настройки ресторана (только для restaurant_admin)
# ──────────────────────────────────────────
@router.get("/me/settings", response_model=RestaurantSettingsResponse)
def get_restaurant_settings(
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает текущие настройки ресторана.
    S1-7: source of truth = первая активная Location ресторана.
    Требует авторизацию: restaurant_admin.
    """
    loc = _get_primary_location(db, restaurant.id)
    return RestaurantSettingsResponse(
        working_hours=loc.working_hours or "",
        delivery_fee=loc.delivery_fee or 0,
        min_order_amount=loc.min_order_amount or 0,
        timezone=loc.timezone or "Asia/Tashkent",
        currency=loc.currency or "UZS",
        language=loc.language or "uz",
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
    минимальная сумма заказа, timezone, currency, language.

    S1-7: запись идёт в Location (source of truth).
    Backward compat до Migration 0015: поля restaurant.working_hours,
    restaurant.min_order_amount, restaurant.currency синхронизируются,
    потому что старый код (orders.py) ещё может их читать.
    Требует авторизацию: restaurant_admin.
    """
    loc = _get_primary_location(db, restaurant.id)

    # ── Валидация ──────────────────────────────────────────────────────────
    if data.delivery_fee is not None and data.delivery_fee < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Стоимость доставки не может быть отрицательной",
        )
    if data.min_order_amount is not None and data.min_order_amount < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Минимальная сумма не может быть отрицательной",
        )
    if data.timezone is not None:
        tz = data.timezone.strip()
        if not _SAFE_TZ_RE_SETTINGS.match(tz):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Невалидный timezone. Пример: Asia/Tashkent, Asia/Almaty, UTC",
            )
        data = data.model_copy(update={"timezone": tz})
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
        data = data.model_copy(update={"currency": cur})
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
        data = data.model_copy(update={"language": lang})

    # ── Запись в Location (S1-7 source of truth) ───────────────────────────
    if data.working_hours is not None:
        loc.working_hours = data.working_hours.strip()
    if data.delivery_fee is not None:
        loc.delivery_fee = data.delivery_fee
    if data.min_order_amount is not None:
        loc.min_order_amount = data.min_order_amount
    if data.timezone is not None:
        loc.timezone = data.timezone
    if data.currency is not None:
        loc.currency = data.currency
    if data.language is not None:
        loc.language = data.language

    # ── Backward compat: синхронизируем поля Restaurant ────────────────────
    # orders.py читает restaurant.min_order_amount и restaurant.currency при
    # создании заказа. Синхронизируем до Migration 0015 (DROP legacy columns).
    # working_hours: роутер меню читает из restaurant — тоже синхронизируем.
    if data.working_hours is not None:
        restaurant.working_hours = loc.working_hours
    if data.min_order_amount is not None:
        restaurant.min_order_amount = loc.min_order_amount
    if data.currency is not None:
        restaurant.currency = loc.currency

    try:
        db.commit()
        db.refresh(loc)
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
        "Restaurant settings updated (S1-7 → Location id=%s): slug=%s "
        "working_hours=%s delivery_fee=%s min_order_amount=%s "
        "timezone=%s currency=%s language=%s",
        loc.id, restaurant.slug,
        loc.working_hours, loc.delivery_fee, loc.min_order_amount,
        loc.timezone, loc.currency, loc.language,
    )

    return RestaurantSettingsUpdateResponse(
        ok=True,
        working_hours=loc.working_hours or "",
        delivery_fee=loc.delivery_fee or 0,
        min_order_amount=loc.min_order_amount or 0,
        timezone=loc.timezone or "Asia/Tashkent",
        currency=loc.currency or "UZS",
        language=loc.language or "uz",
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

    # S1-7: operational settings берутся из первой активной Location.
    # Если Location не найдена (баг данных) — fallback на Restaurant поля,
    # чтобы публичный эндпоинт не упал с 500 (graceful degradation).
    _loc = (
        db.query(Location)
        .filter(
            Location.restaurant_id == restaurant.id,
            Location.is_active == True,
        )
        .order_by(Location.id)
        .first()
    )
    _settings_source = _loc if _loc is not None else restaurant

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
        # White Label branding (остаётся на Restaurant — не операционные настройки)
        "logo_url": restaurant.logo_url,
        "primary_color": restaurant.primary_color,
        "secondary_color": restaurant.secondary_color,
        "accent_color": restaurant.accent_color,
        "welcome_text": restaurant.welcome_text,
        # S1-7: Operational settings из Location (source of truth)
        "working_hours": (_settings_source.working_hours or ""),
        "delivery_fee": (_settings_source.delivery_fee or 0),
        "min_order_amount": (_settings_source.min_order_amount or 0),
        # Валюта ресторана: UZS, KZT, RUB, USD, TRY, AED
        "currency": (getattr(_settings_source, "currency", None) or "UZS"),
        # Язык клиентского UI: uz, ru, en
        "language": (getattr(_settings_source, "language", None) or "uz"),
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

    # S1-2: resolve location_id for this restaurant.
    # Each restaurant has exactly 1 Location (migration 0010 backfill guarantee).
    # We take the first active location; fallback to any location if none active.
    _loc = (
        db.query(Location)
        .filter(Location.restaurant_id == restaurant.id)
        .order_by(Location.id)
        .first()
    )
    if _loc is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Локация ресторана не найдена. Обратитесь к администратору.",
        )

    table = RestaurantTable(
        restaurant_id=restaurant.id,
        location_id=_loc.id,
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


# ══════════════════════════════════════════════════════════════════════════════
# S1-5: LOCATION CRUD
# GET  /me/locations               — список
# POST /me/locations               — создать
# GET  /me/locations/{location_id} — деталь
# PATCH /me/locations/{location_id} — обновить
# DELETE /me/locations/{location_id} — soft-delete
#
# Tenant-изоляция: каждый endpoint фильтрует по restaurant.id из JWT.
# Slug уникален глобально (ck uq_locations_slug) — IntegrityError → 409.
# DELETE: нельзя деактивировать единственную активную Location (I-4).
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/me/locations", response_model=LocationListResponse)
def list_locations(
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> LocationListResponse:
    """
    Возвращает все Location текущего ресторана (активные и неактивные).
    Tenant-изоляция: только свои Location, отсортированы по id.
    Требует авторизацию: restaurant_admin.
    """
    locations = (
        db.query(Location)
        .filter(Location.restaurant_id == restaurant.id)
        .order_by(Location.id)
        .all()
    )
    return LocationListResponse(
        locations=[LocationResponse.model_validate(loc) for loc in locations],
        total=len(locations),
    )


@router.post("/me/locations", response_model=LocationResponse, status_code=201)
def create_location(
    data: LocationCreate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> LocationResponse:
    """
    Создаёт новую Location для текущего ресторана.
    restaurant_id берётся из JWT — клиент не может указать чужой ресторан.
    Slug глобально уникален; конфликт → 409.
    Требует авторизацию: restaurant_admin.
    """
    loc = Location(
        restaurant_id=restaurant.id,
        name=data.name,
        slug=data.slug,
        address=data.address,
        phone=data.phone,
        timezone=data.timezone,
        working_hours=data.working_hours,
        delivery_fee=data.delivery_fee,
        min_order_amount=data.min_order_amount,
        currency=data.currency,
        language=data.language,
        is_waiter_call_enabled=data.is_waiter_call_enabled,
        is_active=True,
    )
    db.add(loc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Slug '{data.slug}' уже занят другой локацией",
        )
    db.refresh(loc)
    logger.info(
        "Location created: id=%s restaurant_id=%s slug=%s",
        loc.id, restaurant.id, loc.slug,
    )
    return LocationResponse.model_validate(loc)


@router.get("/me/locations/{location_id}", response_model=LocationResponse)
def get_location(
    location_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> LocationResponse:
    """
    Возвращает Location по ID.
    Tenant-изоляция: location.restaurant_id должен совпадать с JWT.
    Требует авторизацию: restaurant_admin.
    """
    loc = db.query(Location).filter(
        Location.id == location_id,
        Location.restaurant_id == restaurant.id,
    ).first()
    if not loc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Локация не найдена",
        )
    return LocationResponse.model_validate(loc)


@router.patch("/me/locations/{location_id}", response_model=LocationResponse)
def update_location(
    location_id: int,
    data: LocationUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> LocationResponse:
    """
    Обновляет Location (PATCH-семантика: только переданные поля).
    Tenant-изоляция: location.restaurant_id должен совпадать с JWT.
    Slug uniqueness проверяется на уровне БД; конфликт → 409.
    Требует авторизацию: restaurant_admin.
    """
    loc = db.query(Location).filter(
        Location.id == location_id,
        Location.restaurant_id == restaurant.id,
    ).first()
    if not loc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Локация не найдена",
        )

    # PATCH: обновляем только поля, переданные клиентом (exclude_unset=True)
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(loc, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Slug '{data.slug}' уже занят другой локацией",
        )
    db.refresh(loc)
    logger.info(
        "Location updated: id=%s restaurant_id=%s fields=%s",
        loc.id, restaurant.id, list(update_data.keys()),
    )
    return LocationResponse.model_validate(loc)


@router.delete("/me/locations/{location_id}", status_code=204)
def delete_location(
    location_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> None:
    """
    Soft-delete Location (is_active=False).
    Hard-delete запрещён — FK RESTRICT на reservations.location_id защищает историю.
    Guard I-4: нельзя деактивировать единственную активную Location ресторана.
    Tenant-изоляция: location.restaurant_id должен совпадать с JWT.
    Требует авторизацию: restaurant_admin.
    """
    loc = db.query(Location).filter(
        Location.id == location_id,
        Location.restaurant_id == restaurant.id,
    ).first()
    if not loc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Локация не найдена",
        )
    if not loc.is_active:
        # Уже неактивна — идемпотентно, ничего не делаем
        return

    # I-4: проверяем, что это не единственная активная Location
    active_count = (
        db.query(Location)
        .filter(
            Location.restaurant_id == restaurant.id,
            Location.is_active == True,
        )
        .count()
    )
    if active_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Нельзя деактивировать единственную активную локацию ресторана. "
                "Сначала создайте или активируйте другую локацию."
            ),
        )

    loc.is_active = False
    db.commit()
    logger.info(
        "Location soft-deleted: id=%s restaurant_id=%s slug=%s",
        loc.id, restaurant.id, loc.slug,
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
