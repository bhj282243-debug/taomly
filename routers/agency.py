"""
routers/agency.py — Taomly Platform

Изменения v3:
  - Убрана дублированная логика WEBHOOK_SECRET/WEBHOOK_URL — теперь из config.py
  - Rate limiting на /login и /restaurant-login (10 req/min per IP)
  - Request передаётся в login endpoints для slowapi

Изменения v4 (Stage 3 Sprint 3.1):
  - Rate limiting на /register (5 req/hour per IP)

Изменения v5 (Security):
  - /logout и /restaurant-logout endpoints добавлены
  - get_current_restaurant_admin импортирован для /restaurant-logout
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from limiter import limiter
from auth import (
    create_agency_token,
    create_restaurant_token,
    encrypt_token,
    get_current_agency,
    get_current_restaurant_admin,
    hash_password,
    verify_password,
)
from config import settings
from database import get_db
from models import Agency, Restaurant
from schemas import (
    AgencyLogin,
    AgencyRegister,
    AgencyResponse,
    RestaurantAdminLogin,
    RestaurantAdminResponse,
    RestaurantCreate,
    RestaurantCreateResponse,
    RestaurantUpdate,
    TokenResponse,
)
import handlers
import telegram_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agency", tags=["agency"])


# ──────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────
@router.post("/register", response_model=AgencyResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
def register_agency(request: Request, data: AgencyRegister, db: Session = Depends(get_db)):
    """
    Регистрация нового агентства.
    Rate limited: 5 запросов в час с одного IP.
    """
    existing = db.query(Agency).filter(Agency.owner_email == data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email уже зарегистрирован",
        )

    agency = Agency(
        name=data.name,
        owner_email=data.email,
        owner_password_hash=hash_password(data.password),
    )
    db.add(agency)

    try:
        db.commit()
        db.refresh(agency)
    except Exception:
        logger.exception("Ошибка при регистрации агентства: email=%s", data.email)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании агентства",
        )

    logger.info("Агентство зарегистрировано: agency_id=%s email=%s", agency.id, data.email)
    return agency


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login_agency(request: Request, data: AgencyLogin, db: Session = Depends(get_db)):
    """
    Вход Agency Owner — возвращает JWT.
    Rate limited: 10 запросов в минуту с одного IP.
    """
    agency = db.query(Agency).filter(
        Agency.owner_email == data.email,
        Agency.is_active == True,
    ).first()

    if not agency or not verify_password(data.password, agency.owner_password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )

    logger.info("Agency Owner вошёл: agency_id=%s", agency.id)
    return TokenResponse(access_token=create_agency_token(agency))


@router.post("/restaurant-login", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login_restaurant_admin(
    request: Request,
    data: RestaurantAdminLogin,
    db: Session = Depends(get_db),
):
    """
    Вход ресторанного администратора — возвращает JWT.
    Rate limited: 10 запросов в минуту с одного IP.
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.slug == data.slug.lower().strip(),
        Restaurant.is_active == True,
    ).first()

    if not restaurant or not restaurant.admin_password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный slug или пароль",
        )

    if not verify_password(data.password, restaurant.admin_password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный slug или пароль",
        )

    logger.info(
        "Restaurant Admin вошёл: restaurant_id=%s slug=%s",
        restaurant.id, restaurant.slug,
    )
    return TokenResponse(access_token=create_restaurant_token(restaurant))


# ──────────────────────────────────────────
# ME + LOGOUT
# ──────────────────────────────────────────
@router.get("/me", response_model=AgencyResponse)
def get_agency_me(agency: Agency = Depends(get_current_agency)):
    """Возвращает данные текущего Agency Owner."""
    return agency


@router.post("/logout")
def logout_agency(agency: Agency = Depends(get_current_agency)):
    """
    Выход Agency Owner.

    Stateless: клиент должен удалить токен на своей стороне.
    JWT остаётся технически валидным до истечения (8 часов).
    При появлении Redis — добавить jti в blacklist.
    """
    logger.info("Agency Owner вышел: agency_id=%s", agency.id)
    return {"ok": True, "message": "Выход выполнен. Удалите токен на клиенте."}


@router.post("/restaurant-logout")
def logout_restaurant_admin(restaurant: Restaurant = Depends(get_current_restaurant_admin)):
    """
    Выход ресторанного администратора.
    Stateless: клиент удаляет токен. Серверная инвалидация — в следующем этапе (Redis).
    """
    logger.info("Restaurant Admin вышел: restaurant_id=%s", restaurant.id)
    return {"ok": True, "message": "Выход выполнен. Удалите токен на клиенте."}


# ──────────────────────────────────────────
# РЕСТОРАНЫ — Agency Owner CRUD
# ──────────────────────────────────────────
@router.post("/restaurants", response_model=RestaurantCreateResponse, status_code=status.HTTP_201_CREATED)
def create_restaurant(
    data: RestaurantCreate,
    agency: Agency = Depends(get_current_agency),
    db: Session = Depends(get_db),
):
    """
    Создаёт ресторан под управлением агентства.
    agency_id берётся из JWT — ресторан автоматически привязывается к агентству.
    """
    slug = data.slug.lower().strip()

    if db.query(Restaurant).filter(Restaurant.slug == slug).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slug уже занят",
        )

    custom_domain = None
    if data.custom_domain:
        custom_domain = data.custom_domain.strip().lower()
        if db.query(Restaurant).filter(Restaurant.custom_domain == custom_domain).first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Домен уже занят",
            )

    encrypted_token = None
    if data.telegram_bot_token:
        encrypted_token = encrypt_token(data.telegram_bot_token)

    restaurant = Restaurant(
        agency_id=agency.id,
        name=data.name,
        slug=slug,
        description=data.description,
        phone=data.phone,
        address=data.address,
        admin_password_hash=hash_password(data.admin_password),
        logo_url=data.logo_url,
        primary_color=data.primary_color or "#8B1A2E",
        secondary_color=data.secondary_color or "#FAF6EE",
        accent_color=data.accent_color or "#D4A853",
        welcome_text=data.welcome_text,
        custom_domain=custom_domain,
        telegram_bot_token_encrypted=encrypted_token,
        telegram_dispatcher_id=data.telegram_dispatcher_id,
    )
    db.add(restaurant)

    try:
        db.commit()
        db.refresh(restaurant)
    except Exception:
        logger.exception(
            "Ошибка при создании ресторана: agency_id=%s slug=%s",
            agency.id, slug,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании ресторана",
        )

    logger.info(
        "Ресторан создан: restaurant_id=%s slug=%s agency_id=%s",
        restaurant.id, slug, agency.id,
    )

    webhook_status = "skipped"
    webhook_detail = None
    if data.telegram_bot_token:
        result = telegram_service.register_restaurant_webhook(
            bot_token=data.telegram_bot_token,
            slug=slug,
            webhook_base_url=settings.WEBHOOK_URL,
            webhook_secret=settings.WEBHOOK_SECRET,
            restaurant_name=restaurant.name,
        )
        webhook_status = "ok" if result.ok else "failed"
        webhook_detail = result.detail
        if not result.ok:
            logger.warning(
                "Ресторан id=%s создан, но webhook не зарегистрирован: %s",
                restaurant.id, result.detail,
            )

    response = RestaurantCreateResponse.model_validate(restaurant)
    response.webhook_status = webhook_status
    response.webhook_detail = webhook_detail
    return response


@router.get("/restaurants", response_model=list[RestaurantAdminResponse])
def get_restaurants(
    agency: Agency = Depends(get_current_agency),
    db: Session = Depends(get_db),
):
    """
    Возвращает все рестораны агентства.
    Tenant-изоляция: фильтр agency_id == agency.id из токена.
    """
    return (
        db.query(Restaurant)
        .filter(Restaurant.agency_id == agency.id)
        .order_by(Restaurant.created_at.desc())
        .all()
    )


@router.get("/restaurants/{restaurant_id}", response_model=RestaurantAdminResponse)
def get_restaurant(
    restaurant_id: int,
    agency: Agency = Depends(get_current_agency),
    db: Session = Depends(get_db),
):
    """
    Возвращает ресторан по ID.
    Tenant-изоляция: restaurant_id проверяется против agency_id из токена.
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.agency_id == agency.id,
    ).first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )
    return restaurant


@router.patch("/restaurants/{restaurant_id}", response_model=RestaurantAdminResponse)
def update_restaurant(
    restaurant_id: int,
    data: RestaurantUpdate,
    agency: Agency = Depends(get_current_agency),
    db: Session = Depends(get_db),
):
    """
    Обновляет настройки ресторана.
    Tenant-изоляция + сброс кэша бота при смене токена.
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.agency_id == agency.id,
    ).first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )

    if data.custom_domain:
        custom_domain = data.custom_domain.strip().lower()
        if custom_domain != restaurant.custom_domain:
            if db.query(Restaurant).filter(
                Restaurant.custom_domain == custom_domain
            ).first():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Домен уже занят",
                )
            data.custom_domain = custom_domain

    update_fields = data.model_dump(exclude_none=True)
    token_changed = False
    new_plain_token = None

    if "admin_password" in update_fields:
        restaurant.admin_password_hash = hash_password(update_fields.pop("admin_password"))

    if "telegram_bot_token" in update_fields:
        new_plain_token = update_fields.pop("telegram_bot_token")
        restaurant.telegram_bot_token_encrypted = encrypt_token(new_plain_token)
        token_changed = True

    for field, value in update_fields.items():
        setattr(restaurant, field, value)

    try:
        db.commit()
        db.refresh(restaurant)
    except Exception:
        logger.exception(
            "Ошибка при обновлении ресторана: restaurant_id=%s", restaurant_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении ресторана",
        )

    if token_changed:
        old_bot = handlers._BOT_CACHE.get(restaurant_id)
        if old_bot:
            try:
                old_bot.remove_webhook()
                logger.info("Token changed: старый webhook снят (restaurant_id=%s)", restaurant_id)
            except Exception:
                logger.exception(
                    "Token changed: не удалось снять старый webhook (restaurant_id=%s)",
                    restaurant_id,
                )

        handlers.invalidate_bot_cache(restaurant_id)
        logger.info("BOT_CACHE сброшен после смены токена: restaurant_id=%s", restaurant_id)

        result = telegram_service.register_restaurant_webhook(
            bot_token=new_plain_token,
            slug=restaurant.slug,
            webhook_base_url=settings.WEBHOOK_URL,
            webhook_secret=settings.WEBHOOK_SECRET,
            restaurant_name=restaurant.name,
        )
        if not result.ok:
            logger.warning(
                "Token changed: webhook не зарегистрирован (restaurant_id=%s): %s",
                restaurant_id, result.detail,
            )

    logger.info("Ресторан обновлён: restaurant_id=%s agency_id=%s", restaurant_id, agency.id)
    return restaurant


@router.delete("/restaurants/{restaurant_id}", status_code=status.HTTP_200_OK)
def delete_restaurant(
    restaurant_id: int,
    agency: Agency = Depends(get_current_agency),
    db: Session = Depends(get_db),
):
    """
    Мягкое удаление ресторана (is_active=False).
    Данные сохраняются — ресторан можно реактивировать.
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.agency_id == agency.id,
    ).first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )

    restaurant.is_active = False

    try:
        db.commit()
    except Exception:
        logger.exception(
            "Ошибка при деактивации ресторана: restaurant_id=%s", restaurant_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при деактивации ресторана",
        )

    if restaurant.telegram_bot_token_encrypted:
        try:
            from auth import decrypt_token as _decrypt_token
            bot_token = _decrypt_token(restaurant.telegram_bot_token_encrypted)
            telegram_service.remove_restaurant_webhook(
                bot_token=bot_token,
                slug=restaurant.slug,
                restaurant_name=restaurant.name,
            )
        except Exception:
            logger.exception(
                "Не удалось снять webhook при деактивации restaurant_id=%s — продолжаем",
                restaurant_id,
            )

    handlers.invalidate_bot_cache(restaurant_id)
    logger.info(
        "Ресторан деактивирован: restaurant_id=%s agency_id=%s",
        restaurant_id, agency.id,
    )
    return {"ok": True, "detail": "Ресторан деактивирован"}
