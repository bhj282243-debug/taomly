"""
routers/reservations.py — Taomly Platform

Изменения v3 (S1-4: reservations.location_id):
  - POST /: принимает X-Location-Id header — явный источник Location.
    Location резолвится из БД и валидируется: location.restaurant_id == restaurant.id.
    Reservation создаётся с location_id = location.id.
  - GET /restaurant/{restaurant_id}: фильтр по restaurant_id сохранён (brand-level admin).
  - PATCH /{reservation_id}/status: без изменений (tenant-изоляция по restaurant_id).

Предыдущие изменения (v2):
  - POST /: добавлен get_telegram_user — restaurant берётся из TelegramUser,
    restaurant_id убран из схемы ReservationCreate
  - GET /restaurant/{restaurant_id}: добавлена JWT-авторизация + tenant-проверка
  - PATCH /{reservation_id}/status: добавлена JWT-авторизация + tenant-проверка → закрыт IDOR
  - Добавлены статусные переходы VALID_STATUS_TRANSITIONS
  - Логирование ошибок через logger.exception
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from auth import TelegramUser, get_current_restaurant_admin, get_telegram_user
from database import get_db
from limiter import limiter
from models import Location, Reservation, Restaurant
from schemas import ReservationCreate, ReservationResponse, ReservationStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter()

from status_transitions import RESERVATION_STATUS_TRANSITIONS as VALID_STATUS_TRANSITIONS


# ──────────────────────────────────────────
# POST / — создать бронь (клиент Mini App)
# ──────────────────────────────────────────
@router.post("/", response_model=ReservationResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def create_reservation(
    request: Request,
    data: ReservationCreate,
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
    x_location_id: int = Header(..., alias="X-Location-Id"),
):
    """
    Создаёт бронь стола.

    restaurant берётся из TelegramUser (верифицирован через initData).
    restaurant_id убран из тела запроса — клиент не может указать чужой ресторан.

    S1-4: X-Location-Id обязателен. Location резолвится из БД и валидируется:
      location.restaurant_id == restaurant.id — защита от cross-brand injection.
      location.is_active == True — деактивированная Location не принимает брони.
    """
    restaurant = tg_user.restaurant

    # ── S1-4: Resolve and validate Location ───────────────────────────────
    location = db.query(Location).filter(
        Location.id == x_location_id,
        Location.restaurant_id == restaurant.id,
        Location.is_active == True,  # noqa: E712
    ).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location не найдена или недоступна для этого ресторана",
        )

    reservation = Reservation(
        restaurant_id=restaurant.id,
        location_id=location.id,        # S1-4 canonical
        client_name=data.client_name,
        client_phone=data.client_phone,
        guests_count=data.guests_count,
        reservation_time=data.reservation_time,
        comment=data.comment,
        status="new",
    )
    db.add(reservation)

    try:
        db.commit()
        db.refresh(reservation)
    except Exception:
        logger.exception(
            "Ошибка при создании брони: restaurant_id=%s client=%s",
            restaurant.id,
            data.client_name,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании брони",
        )

    logger.info(
        "Бронь создана: reservation_id=%s restaurant_id=%s location_id=%s client=%s",
        reservation.id, restaurant.id, location.id, data.client_name,
    )
    return reservation


# ──────────────────────────────────────────
# GET /restaurant/{restaurant_id} — список броней (админка)
# ──────────────────────────────────────────
@router.get("/restaurant/{restaurant_id}", response_model=List[ReservationResponse])
def get_reservations(
    restaurant_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает брони ресторана.
    Tenant-изоляция: restaurant_id из URL проверяется против токена JWT.
    """
    if restaurant.id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к данным этого ресторана",
        )

    return (
        db.query(Reservation)
        .filter(Reservation.restaurant_id == restaurant_id)
        .order_by(Reservation.reservation_time)
        .all()
    )


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
    Меняет статус брони.
    Tenant-изоляция: бронь ищется только среди броней ресторана из токена.
    Проверяется допустимость перехода статуса.
    """
    reservation = db.query(Reservation).filter(
        Reservation.id == reservation_id,
        # Tenant-изоляция — нельзя менять статус чужой брони
        Reservation.restaurant_id == restaurant.id,
    ).first()

    if not reservation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Бронь не найдена",
        )

    allowed = VALID_STATUS_TRANSITIONS.get(reservation.status, [])
    if data.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Переход «{reservation.status}» → «{data.status}» невозможен. "
                f"Допустимые: {allowed if allowed else 'нет (финальный статус)'}"
            ),
        )

    old_status = reservation.status
    reservation.status = data.status

    try:
        db.commit()
        db.refresh(reservation)
    except Exception:
        logger.exception(
            "Ошибка при обновлении статуса брони: reservation_id=%s", reservation_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении статуса",
        )

    logger.info(
        "Статус брони изменён: reservation_id=%s %s → %s restaurant_id=%s",
        reservation_id, old_status, data.status, restaurant.id,
    )
    return reservation
