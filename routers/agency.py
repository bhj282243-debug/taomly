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
from typing import Optional

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from limiter import limiter
from auth import (
    create_agency_token,
    create_restaurant_token,
    decrypt_token,
    encrypt_token,
    get_current_agency,
    get_current_restaurant_admin,
    hash_password,
    revoke_token,
    verify_password,
)
from config import settings
from database import get_db
from models import Agency, Location, Restaurant
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
# FOUNDATION TASK 8 — Telegram Auth Hardening
#
# Cross-restaurant isolation (auth.get_telegram_user / verify_telegram_init_data)
# relies on each restaurant having a DISTINCT bot token: the Telegram HMAC
# is computed with the bot token of the restaurant named in X-Restaurant-Id,
# so initData from restaurant A only verifies against restaurant B's context
# if A and B share the exact same bot token. Nothing previously stopped an
# agency admin from pasting the same bot token into two restaurants (e.g.
# copy-paste error, or reusing one bot while testing). This check closes
# that gap at the point tokens are set, without changing the auth algorithm
# or adding any new storage/migration.
# ──────────────────────────────────────────
def _bot_token_in_use(
    db: Session,
    plain_token: str,
    exclude_restaurant_id: int | None = None,
) -> bool:
    """
    True если plain_token уже используется другим рестораном.

    Fernet-шифрование не детерминировано — сравнить ciphertext напрямую
    нельзя, поэтому расшифровываем существующие токены и сравниваем
    в открытом виде. Приемлемо при текущем масштабе (десятки-сотни
    ресторанов): полный пересчёт архитектуры/индекса не требуется.
    """
    query = db.query(Restaurant).filter(Restaurant.telegram_bot_token_encrypted.isnot(None))
    if exclude_restaurant_id is not None:
        query = query.filter(Restaurant.id != exclude_restaurant_id)
    for other in query.all():
        try:
            if decrypt_token(other.telegram_bot_token_encrypted) == plain_token:
                return True
        except HTTPException:
            # Повреждённый/нерасшифровываемый токен другого ресторана —
            # не блокируем текущую операцию из-за чужой проблемы.
            continue
    return False

# Фиктивный хеш для timing-safe проверки пароля (SEC-6).
# bcrypt отрабатывает даже когда email/slug не найден — выравнивает время ответа.
#
# Foundation Gate P0-2: предыдущий хеш имел 51 символ после префикса вместо 53
# (checksum 29 вместо 31). passlib выбрасывал ValueError: malformed bcrypt hash
# → неизвестный email/slug возвращал HTTP 500 вместо 401.
# Текущий хеш структурно валиден: $2b$12$ (7) + 22-char salt + 31-char checksum = 60 total.
# bcrypt.verify() вернёт False (пароль не совпадает) — без исключения.
# Хеш не является хешем реального пароля; используется только для timing equality.
_DUMMY_HASH: str = "$2b$12$abcdefghijklmnopqrstuvAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


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
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email уже зарегистрирован",
        )
    except Exception as exc:
        logger.exception("Ошибка при регистрации агентства: email=%s", data.email)
        sentry_sdk.capture_exception(exc)
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

    # SEC-6: verify_password вызывается всегда — защита от timing enumeration.
    # При отсутствии agency bcrypt отрабатывает на фиктивном хеше, выравнивая время ответа.
    password_ok = verify_password(
        data.password,
        agency.owner_password_hash if agency else _DUMMY_HASH,
    )
    if not agency or not password_ok:
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

    # SEC-6: verify_password вызывается всегда — защита от timing enumeration по slug.
    password_ok = verify_password(
        data.password,
        restaurant.admin_password_hash if (restaurant and restaurant.admin_password_hash) else _DUMMY_HASH,
    )
    if not restaurant or not restaurant.admin_password_hash or not password_ok:
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
def logout_agency(
    request: Request,
    agency: Agency = Depends(get_current_agency),
    db: Session = Depends(get_db),
):
    """
    Выход Agency Owner.

    Серверная инвалидация: jti токена записывается в revoked_tokens.
    Все последующие запросы с этим токеном вернут 401, даже если токен
    ещё не истёк по времени.

    payload берётся из request.state.jwt_payload, установленного в
    get_current_agency() — нет повторного decode и SELECT к revoked_tokens.
    """
    payload = request.state.jwt_payload
    revoke_token(payload, db)
    logger.info("Agency Owner вышел: agency_id=%s jti=%s", agency.id, payload.get("jti"))
    return {"ok": True, "message": "Выход выполнен. Токен инвалидирован."}


@router.post("/restaurant-logout")
def logout_restaurant_admin(
    request: Request,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Выход ресторанного администратора.

    Серверная инвалидация: jti токена записывается в revoked_tokens.
    payload берётся из request.state.jwt_payload — нет повторного decode.
    """
    payload = request.state.jwt_payload
    revoke_token(payload, db)
    logger.info("Restaurant Admin вышел: restaurant_id=%s jti=%s", restaurant.id, payload.get("jti"))
    return {"ok": True, "message": "Выход выполнен. Токен инвалидирован."}


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
        if _bot_token_in_use(db, data.telegram_bot_token):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Этот Telegram Bot Token уже используется другим рестораном. "
                    "У каждого ресторана должен быть собственный бот (создайте "
                    "нового через @BotFather)."
                ),
            )
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

    # S1-6: первая Location создаётся атомарно вместе с Restaurant.
    # slug Location = slug Restaurant (backward compat: /webhook/{slug} ищет по Restaurant.slug,
    # но Location.slug должен совпадать для будущего роутинга по QR/URL).
    # Telegram-конфигурация дублируется в Location (ADR-001: 1 Location = 1 Bot).
    # is_waiter_call_enabled берётся из Restaurant-уровня — REST схема передаёт его в Restaurant.
    db.flush()  # получаем restaurant.id без commit
    default_location = Location(
        restaurant_id=restaurant.id,
        name=restaurant.name,
        slug=slug,
        is_active=True,
        address=restaurant.address,
        phone=restaurant.phone,
        timezone="Asia/Tashkent",
        delivery_fee=0,
        min_order_amount=0,
        currency="UZS",
        language="uz",
        is_waiter_call_enabled=getattr(restaurant, "is_waiter_call_enabled", False),
        telegram_bot_token_encrypted=encrypted_token,
        telegram_dispatcher_id=restaurant.telegram_dispatcher_id,
    )
    db.add(default_location)

    try:
        db.commit()
        db.refresh(restaurant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Slug или домен уже заняты",
        )
    except Exception as exc:
        logger.exception(
            "Ошибка при создании ресторана: agency_id=%s slug=%s",
            agency.id, slug,
        )
        sentry_sdk.capture_exception(exc)
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
        if _bot_token_in_use(db, new_plain_token, exclude_restaurant_id=restaurant_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Этот Telegram Bot Token уже используется другим рестораном. "
                    "У каждого ресторана должен быть собственный бот (создайте "
                    "нового через @BotFather)."
                ),
            )
        restaurant.telegram_bot_token_encrypted = encrypt_token(new_plain_token)
        token_changed = True

    for field, value in update_fields.items():
        setattr(restaurant, field, value)

    # S1-8: при смене Telegram credentials — синхронно обновить Location в той же транзакции.
    # Invariant I-2: Restaurant + Location обновляются атомарно, коммит один.
    location_for_cache: Optional["Location"] = None
    if token_changed or ("telegram_dispatcher_id" in update_fields):
        default_location = (
            db.query(Location)
            .filter(
                Location.restaurant_id == restaurant_id,
                Location.is_active == True,
            )
            .first()
        )
        if default_location:
            location_for_cache = default_location
            if token_changed:
                default_location.telegram_bot_token_encrypted = (
                    restaurant.telegram_bot_token_encrypted
                )
            if "telegram_dispatcher_id" in update_fields:
                default_location.telegram_dispatcher_id = restaurant.telegram_dispatcher_id
            logger.info(
                "S1-8: Telegram credentials синхронизированы в Location (location_id=%s)",
                default_location.id,
            )
        else:
            logger.warning(
                "S1-8: активная Location для restaurant_id=%s не найдена — "
                "синхронизация credentials пропущена",
                restaurant_id,
            )

    try:
        db.commit()
        db.refresh(restaurant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Slug или домен уже заняты",
        )
    except Exception as exc:
        logger.exception(
            "Ошибка при обновлении ресторана: restaurant_id=%s", restaurant_id
        )
        sentry_sdk.capture_exception(exc)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении ресторана",
        )

    if token_changed:
        # S1-8: инвалидируем кэш по location.id (Invariant I-3).
        # Также инвалидируем по restaurant_id для backward compat
        # (legacy код мог положить бот в кэш по restaurant.id через get_restaurant_bot).
        _cache_key = location_for_cache.id if location_for_cache else restaurant_id
        old_bot = handlers._BOT_CACHE.get(_cache_key)
        if old_bot:
            try:
                old_bot.remove_webhook()
                logger.info(
                    "Token changed: старый webhook снят (cache_key=%s)", _cache_key
                )
            except Exception:
                logger.exception(
                    "Token changed: не удалось снять старый webhook (cache_key=%s)",
                    _cache_key,
                )

        handlers.invalidate_bot_cache(_cache_key)
        # Также чистим restaurant_id из кэша если он там есть (backward compat)
        if _cache_key != restaurant_id and restaurant_id in handlers._BOT_CACHE:
            handlers.invalidate_bot_cache(restaurant_id)
        logger.info(
            "BOT_CACHE сброшен после смены токена: cache_key=%s", _cache_key
        )

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
    except Exception as exc:
        logger.exception(
            "Ошибка при деактивации ресторана: restaurant_id=%s", restaurant_id
        )
        sentry_sdk.capture_exception(exc)
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

    # S1-8: инвалидируем кэш по location.id (Invariant I-3).
    # При деактивации ресторана ищем его Location чтобы сбросить кэш по location.id.
    # Backward compat: также сбрасываем по restaurant_id.
    _loc_for_delete = (
        db.query(Location)
        .filter(Location.restaurant_id == restaurant_id)
        .first()
    )
    if _loc_for_delete:
        handlers.invalidate_bot_cache(_loc_for_delete.id)
    handlers.invalidate_bot_cache(restaurant_id)
    logger.info(
        "Ресторан деактивирован: restaurant_id=%s agency_id=%s",
        restaurant_id, agency.id,
    )
    return {"ok": True, "detail": "Ресторан деактивирован"}
