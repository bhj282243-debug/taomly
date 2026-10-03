"""
modules/reservations/service.py — Taomly Platform

Phase 15 (Spec v3.2): service layer бронирования.
Router → Service → DB/Models. Бизнес-логика создания брони и гостевых
лимитов находится здесь, а не в router.

MC-07 — гостевые лимиты (утверждены Owner), только для Guest-запроса:
  1. не более RESERVATION_GUEST_MAX_CREATED_PER_60_MIN (5) созданных броней
     за последние 60 минут на пару (телефон только из цифр + Location);
     отменённые брони входят в счёт; превышение → HTTP 429;
  2. не более RESERVATION_GUEST_MAX_ACTIVE (3) активных броней на ту же пару;
     превышение → HTTP 409;
  3. подсчёт идёт по ВСЕМ броням пары независимо от создателя (Guest или
     Verified), но ограничение применяется только к запросу Guest;
  4. Verified Telegram user этим лимитам не подчиняется;
  5. подсчёт в PostgreSQL под transaction-level advisory lock по паре —
     параллельные запросы не обходят лимиты. Redis, in-memory счётчики и
     отдельная таблица счётчиков не используются.

Существующий IP-лимит 10/мин (slowapi) остаётся в router и не заменяется.
"""

from datetime import UTC, datetime, timedelta
import hashlib
import logging

from fastapi import HTTPException, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from auth import TelegramUser
from config import settings
from models import Location, Reservation
from models.operations import phone_digits
from schemas import ReservationCreate

logger = logging.getLogger(__name__)

# Активные статусы (Spec §14.2: requested, confirmed).
# 'new' — прежнее имя состояния 'requested' (OD-01): переименование статуса
# выполняется отдельной стадией Spec; до неё в БД существует только 'new'
# (CHECK check_reservation_status). 'requested' включён заранее и ничего не меняет
# до появления этого значения в БД. Других статусов не вводится.
ACTIVE_RESERVATION_STATUSES = ("new", "requested", "confirmed")

# Окно «последние 60 минут» — константа сервиса; в config.py выносятся только 5 и 3.
GUEST_LIMIT_WINDOW = timedelta(minutes=60)

_DETAIL_CREATED_LIMIT = (
    "Слишком много броней за последний час для этого номера телефона. "
    "Попробуйте позже."
)
_DETAIL_ACTIVE_LIMIT = "Достигнут лимит активных броней для этого номера телефона."


# ──────────────────────────────────────────
# Advisory lock (PostgreSQL)
# ──────────────────────────────────────────
def guest_limit_lock_key(location_id: int, digits: str) -> int:
    """
    Детерминированный 64-битный ключ advisory lock для пары (Location, телефон).

    Разные пары дают разные ключи и друг друга не блокируют; случайная коллизия
    ключей приводит лишь к лишней сериализации, но не к нарушению лимитов.
    """
    raw = f"taomly:reservation_guest_limit:{location_id}:{digits}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


def _acquire_guest_limit_lock(db: Session, location_id: int, digits: str) -> None:
    """
    pg_advisory_xact_lock — блокирующий, транзакционный (снимается на commit/rollback).

    Блокирующий вариант выбран намеренно (Spec §13.3): параллельные запросы одной
    пары выстраиваются в очередь, а не отклоняются. Существующий
    utils.pg_advisory_lock использует pg_try_advisory_xact_lock (не ждёт) и здесь
    не подходит. На диалектах без advisory locks (SQLite) шаг пропускается.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": guest_limit_lock_key(location_id, digits)},
    )


# ──────────────────────────────────────────
# MC-07: гостевые лимиты
# ──────────────────────────────────────────
def enforce_guest_limits(
    db: Session,
    *,
    location_id: int,
    digits: str,
    now: datetime | None = None,
) -> None:
    """
    Проверяет оба гостевых лимита для пары (location_id, digits).

    Вызывать только для Guest-запроса и внутри той же транзакции, что и INSERT
    брони: lock держится до commit, поэтому SELECT COUNT → INSERT атомарен
    относительно других запросов той же пары.

    Порядок: сначала лимит 60 минут (429), затем лимит активных (409);
    если превышены оба, возвращается 429.
    Ответы не раскрывают числовые значения лимитов и данные других броней.
    """
    now = now or datetime.now(UTC)
    _acquire_guest_limit_lock(db, location_id, digits)

    window_start = now - GUEST_LIMIT_WINDOW
    created_recent = (
        db.query(func.count(Reservation.id))
        .filter(
            Reservation.location_id == location_id,
            Reservation.client_phone_digits == digits,
            Reservation.created_at >= window_start,
        )
        .scalar()
        or 0
    )
    if created_recent >= settings.RESERVATION_GUEST_MAX_CREATED_PER_60_MIN:
        logger.warning("MC-07: guest created-limit (60 min) reached: location_id=%s", location_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_DETAIL_CREATED_LIMIT,
        )

    active = (
        db.query(func.count(Reservation.id))
        .filter(
            Reservation.location_id == location_id,
            Reservation.client_phone_digits == digits,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        )
        .scalar()
        or 0
    )
    if active >= settings.RESERVATION_GUEST_MAX_ACTIVE:
        logger.warning("MC-07: guest active-limit reached: location_id=%s", location_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_DETAIL_ACTIVE_LIMIT,
        )


# ──────────────────────────────────────────
# Создание брони
# ──────────────────────────────────────────
def create_reservation(
    db: Session,
    *,
    tg_user: TelegramUser,
    location_id: int,
    data: ReservationCreate,
    now: datetime | None = None,
) -> Reservation:
    """
    Создаёт бронь. Поведение для Verified-запроса и ответы при ошибках Location /
    commit не изменены (перенос из router без изменения контракта).

    S1-4: Location резолвится из БД и валидируется:
      location.restaurant_id == restaurant.id — защита от cross-brand injection;
      location.is_active — деактивированная Location не принимает брони.

    MC-07: для Guest (tg_user.is_guest) до INSERT проверяются гостевые лимиты.
    `now` — только для детерминированных тестов окна 60 минут.
    """
    restaurant = tg_user.restaurant

    location = db.query(Location).filter(
        Location.id == location_id,
        Location.restaurant_id == restaurant.id,
        Location.is_active == True,  # noqa: E712
    ).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location не найдена или недоступна для этого ресторана",
        )

    digits = phone_digits(data.client_phone)

    if tg_user.is_guest:
        enforce_guest_limits(db, location_id=location.id, digits=digits, now=now)

    reservation = Reservation(
        restaurant_id=restaurant.id,
        location_id=location.id,        # S1-4 canonical
        client_name=data.client_name,
        client_phone=data.client_phone,
        client_phone_digits=digits,
        guests_count=data.guests_count,
        reservation_time=data.reservation_time,
        comment=data.comment,
        status="new",
    )
    db.add(reservation)

    try:
        db.commit()
        db.refresh(reservation)
    except Exception as exc:
        logger.exception(
            "Ошибка при создании брони: restaurant_id=%s client=%s",
            restaurant.id,
            data.client_name,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании брони",
        ) from exc

    logger.info(
        "Бронь создана: reservation_id=%s restaurant_id=%s location_id=%s client=%s",
        reservation.id, restaurant.id, location.id, data.client_name,
    )
    return reservation
