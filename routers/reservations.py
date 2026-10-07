"""
routers/reservations.py — Taomly Platform

Изменения v3 (S1-4: reservations.location_id):
  - POST /: принимает X-Location-Id header — явный источник Location.
    Location резолвится из БД и валидируется: location.restaurant_id == restaurant.id.
    Reservation создаётся с location_id = location.id.
  - GET /restaurant/{restaurant_id}: фильтр по restaurant_id сохранён (brand-level admin).
  - PATCH /{reservation_id}/status: без изменений (tenant-изоляция по restaurant_id).

Изменения v4 (Phase 15, Slice B, R1 — подключение service):
  - POST /: необязательный header Idempotency-Key (^[A-Za-z0-9_-]{8,64}$, иначе 422).
    Новая бронь -> 201; точный повтор -> 200 + Idempotent-Replayed: true; повтор с иными
    данными -> 409. Создание — modules/reservations/service.create_reservation_idempotent.
  - PATCH /{reservation_id}/status: логика переходов — service.transition_status;
    недопустимый переход -> 409 (было 400); цели new/requested -> 422 (схема).
    Principal restaurant admin (actor_ref = restaurant_admin:<restaurant_id>) строится
    из токена; доступ проверяет modules.access.assert_location_access внутри service.
  - GET /restaurant/{restaurant_id}: без изменений (list_reservations в R1 не вводится).

Предыдущие изменения (v2):
  - POST /: добавлен get_telegram_user — restaurant берётся из TelegramUser,
    restaurant_id убран из схемы ReservationCreate
  - GET /restaurant/{restaurant_id}: добавлена JWT-авторизация + tenant-проверка
  - PATCH /{reservation_id}/status: добавлена JWT-авторизация + tenant-проверка → закрыт IDOR
  - Добавлены статусные переходы VALID_STATUS_TRANSITIONS
  - Логирование ошибок через logger.exception
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from auth import TelegramUser, get_current_restaurant_admin, get_telegram_user
from database import get_db
from limiter import limiter
from models import Location, Reservation, Restaurant
from modules.access import restaurant_admin_principal
from modules.reservations.service import create_reservation_idempotent, transition_status
from schemas import ReservationCreate, ReservationResponse, ReservationStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter()

# Phase 15 (Slice B): формат Idempotency-Key (Spec v2 §14.1). Нарушение -> 422.
_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


# ──────────────────────────────────────────
# POST / — создать бронь (клиент Mini App)
# ──────────────────────────────────────────
@router.post("/", response_model=ReservationResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def create_reservation(
    request: Request,
    response: Response,
    data: ReservationCreate,
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
    x_location_id: int = Header(..., alias="X-Location-Id"),
    idempotency_key: str | None = Header(
        None, alias="Idempotency-Key", pattern=_IDEMPOTENCY_KEY_PATTERN
    ),
):
    """
    Создаёт бронь стола.

    restaurant берётся из TelegramUser (верифицирован через initData),
    restaurant_id в теле запроса нет — клиент не может указать чужой ресторан.

    S1-4: X-Location-Id обязателен. Location резолвится из БД и валидируется
    (чужая / неактивная -> 404).

    Phase 15 (MC-07): для Guest (запрос без initData) действуют лимиты
    5 созданных броней за 60 минут (429) и 3 активные (409) на пару
    (телефон + Location). Verified user им не подчиняется. IP-лимит 10/мин сохранён.

    Phase 15 (Slice B): Idempotency-Key (header, необязателен).
      - новая бронь -> 201;
      - точный повтор (тот же Location + ключ + те же 6 полей) -> 200 и заголовок
        Idempotent-Replayed: true, существующая бронь, новая не создаётся;
      - ключ занят, но данные иные -> 409;
      - is_reservation_enabled = false -> 403 (для новой брони).
    Вся бизнес-логика — в modules/reservations/service.py.
    """
    result = create_reservation_idempotent(
        db,
        tg_user=tg_user,
        location_id=x_location_id,
        data=data,
        idempotency_key=idempotency_key,
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return result.reservation


# ──────────────────────────────────────────
# GET /restaurant/{restaurant_id} — список броней (админка)
# ──────────────────────────────────────────
@router.get("/restaurant/{restaurant_id}", response_model=List[ReservationResponse])
def get_reservations(
    restaurant_id: int,
    # S1-5: необязательный фильтр по Location.
    # Без параметра → Brand-level (все брони ресторана, backward compat).
    location_id: Optional[int] = Query(None, alias="location_id"),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает брони ресторана.
    Tenant-изоляция: restaurant_id из URL проверяется против токена JWT.
    S1-5: опциональный ?location_id=<id> — фильтр по Location (tenant-изолировано).
    """
    if restaurant.id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к данным этого ресторана",
        )

    # S1-5: валидируем location_id если передан (tenant isolation, I-2)
    if location_id is not None:
        loc = db.query(Location).filter(
            Location.id == location_id,
            Location.restaurant_id == restaurant.id,
        ).first()
        if not loc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Локация не найдена",
            )

    query = (
        db.query(Reservation)
        .filter(Reservation.restaurant_id == restaurant_id)
    )

    # S1-5: применяем фильтр только если явно передан
    if location_id is not None:
        query = query.filter(Reservation.location_id == location_id)

    return query.order_by(Reservation.reservation_time).all()


# ──────────────────────────────────────────
# PATCH /{reservation_id}/status — сменить статус (админка)
# ──────────────────────────────────────────
@router.patch("/{reservation_id}/status", response_model=ReservationResponse)
def update_status(
    reservation_id: int,
    data: ReservationStatusUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Меняет статус брони (админка).

    Tenant-изоляция и доступ (Restaurant -> Location -> Object) проверяются в
    service.transition_status через modules.access.assert_location_access: чужая бронь /
    чужой ресторан -> 404, Location вне location_scope -> 403.
    Недопустимый или повторный переход -> 409. Цели new / requested схема отклоняет (422).
    Principal строится из токена (actor_ref = restaurant_admin:<restaurant_id>);
    actor от клиента не принимается.
    """
    return transition_status(
        db,
        restaurant_admin_principal(restaurant),
        reservation_id,
        data.status,
    )
