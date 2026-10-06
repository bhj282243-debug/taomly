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

Phase 15, Slice B (R1) — SPEC v2 (PHASE_15_SLICE_B_RESERVATION_CORE_...):
  - создание брони идёт через create_reservation_idempotent (единственная реализация);
    create_reservation сохранён как тонкая обёртка с прежним контрактом -> Reservation;
  - новая бронь получает статус 'requested' (legacy 'new' остаётся допустимым в БД до R2);
  - Idempotency-Key, Option B: уникальность (location_id, idempotency_key); повтор с теми
    же шестью полями — replay, иначе 409; гонку разрешает partial unique index;
  - is_reservation_enabled = FALSE блокирует только создание НОВОЙ брони (403);
  - transition_status: переходы по status_transitions.py, lifecycle timestamps
    (write-once, UTC), доступ через modules.access.assert_location_access.

Порядок создания (Spec v2 §13.3):
  Location -> phone digits -> replay#1 -> flag (403) -> [Guest: advisory lock ->
  replay#2 -> MC-07] -> INSERT -> COMMIT -> (IntegrityError по ключу -> replay#3).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import logging

from fastapi import HTTPException, status
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import TelegramUser
from config import settings
from models import Location, Reservation
from models.operations import phone_digits
from modules.access import Principal, assert_location_access
from schemas import ReservationCreate
from status_transitions import RESERVATION_STATUS_TRANSITIONS

logger = logging.getLogger(__name__)

# Активные статусы (Spec §14.2: requested, confirmed).
# R1 (Slice B): новые брони создаются как 'requested'; 'new' — прежнее имя этого состояния,
# остаётся допустимым в БД до R2 (миграция 0033) и поэтому считается активным.
# Состав кортежа не менялся; 'new' убирается в R2. Других статусов не вводится.
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
# Phase 15, Slice B (R1): создание брони, idempotency, переходы статусов
# ──────────────────────────────────────────
_DETAIL_LOCATION_UNAVAILABLE = "Location не найдена или недоступна для этого ресторана"
_DETAIL_RESERVATION_DISABLED = "Бронирование не включено для этой локации."
_DETAIL_IDEMPOTENCY_CONFLICT = "Idempotency-Key уже использован с другими данными"
_DETAIL_RESERVATION_NOT_FOUND = "Бронь не найдена"

# Имя partial unique index (миграция 0032): (location_id, idempotency_key).
_IDEMPOTENCY_INDEX = "uq_reservations_idempotency"

# Целевой статус -> lifecycle timestamp (Spec v2 §7.4). requested отдельного поля не имеет
# (его роль играет created_at).
_STATUS_TIMESTAMP_FIELDS = {
    "confirmed": "confirmed_at",
    "seated": "seated_at",
    "completed": "completed_at",
    "cancelled": "cancelled_at",
    "no_show": "no_show_at",
}


@dataclass(frozen=True)
class ReservationCreateResult:
    """Результат создания: бронь и признак replay (router: 201 / 200 + Idempotent-Replayed)."""

    reservation: Reservation
    replayed: bool


def _client_principal(tg_user: TelegramUser) -> Principal:
    """
    Контекст доступа клиентского запроса (Guest / Verified) для assert_location_access.

    location_scope=None: клиент вправе выбрать любую активную Location своего Restaurant
    (как и раньше). actor_ref клиента Spec не определяет; helper его не читает.
    """
    actor_ref = "guest" if tg_user.is_guest else f"telegram:{tg_user.id}"
    return Principal(restaurant=tg_user.restaurant, location_scope=None, actor_ref=actor_ref)


def _resolve_location(db: Session, tg_user: TelegramUser, location_id: int) -> Location:
    """
    S1-4: Location загружается из БД и проверяется через canonical assert_location_access
    (Restaurant -> Location). Ответы прежние: чужая / несуществующая / неактивная
    Location -> 404 с прежним текстом.
    """
    unavailable = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=_DETAIL_LOCATION_UNAVAILABLE,
    )
    location = db.get(Location, location_id)
    if location is None:
        raise unavailable
    try:
        assert_location_access(
            _client_principal(tg_user),
            restaurant_id=tg_user.restaurant.id,
            location=location,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise unavailable from exc
        raise
    if not location.is_active:
        raise unavailable
    return location


def _find_by_idempotency_key(
    db: Session, location_id: int, idempotency_key: str
) -> Reservation | None:
    """Replay lookup (Option B): по (location_id, idempotency_key), без телефона и identity."""
    return (
        db.query(Reservation)
        .filter(
            Reservation.location_id == location_id,
            Reservation.idempotency_key == idempotency_key,
        )
        .first()
    )


def _utc(value: datetime | None) -> datetime | None:
    """Приводит datetime к UTC (naive считается UTC) — для сравнения независимо от диалекта БД."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _replay_or_conflict(
    existing: Reservation,
    *,
    digits: str,
    telegram_id: int | None,
    data: ReservationCreate,
) -> ReservationCreateResult:
    """
    Сравнивает ровно шесть полей (client_phone_digits, client_telegram_id, client_name,
    guests_count, reservation_time, comment). Совпали все -> replay существующей брони;
    иначе 409 без данных брони. Новая бронь в обоих случаях не создаётся.
    """
    same = (
        existing.client_phone_digits == digits
        and existing.client_telegram_id == telegram_id
        and existing.client_name == data.client_name
        and existing.guests_count == data.guests_count
        and _utc(existing.reservation_time) == _utc(data.reservation_time)
        and existing.comment == data.comment
    )
    if not same:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_DETAIL_IDEMPOTENCY_CONFLICT,
        )
    return ReservationCreateResult(reservation=existing, replayed=True)


def _is_idempotency_violation(exc: IntegrityError) -> bool:
    """True, если IntegrityError вызван именно partial unique index по idempotency_key."""
    orig = exc.orig
    diag = getattr(orig, "diag", None)
    if getattr(diag, "constraint_name", None) == _IDEMPOTENCY_INDEX:
        return True
    message = str(orig)
    return _IDEMPOTENCY_INDEX in message or "reservations.idempotency_key" in message


def _raise_create_failed(db: Session, exc: Exception, restaurant_id: int, client_name: str):
    """Прежняя обработка сбоя commit: лог, rollback, 500 (контракт не изменён)."""
    logger.exception(
        "Ошибка при создании брони: restaurant_id=%s client=%s",
        restaurant_id,
        client_name,
    )
    db.rollback()
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Ошибка при создании брони",
    ) from exc


def create_reservation_idempotent(
    db: Session,
    *,
    tg_user: TelegramUser,
    location_id: int,
    data: ReservationCreate,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> ReservationCreateResult:
    """
    Создаёт бронь (единственная реализация создания). Порядок — Spec v2 §13.3:

      1. Location (404) -> 2. digits -> 3. replay#1 (только при ключе) ->
      4. is_reservation_enabled (403) -> 5. Guest: advisory lock -> replay#2 -> MC-07 ->
      6. INSERT (status='requested') -> 7. COMMIT ->
      8. IntegrityError по uq_reservations_idempotency: rollback -> replay#3.

    replay#1 стоит до проверки флага и MC-07: повтор не получает ложный 403/409/429 и не
    расходует лимит. replay#2 нужен для параллельного дубля, ждавшего lock. MC-07
    (enforce_guest_limits, advisory lock) не изменён; pg_advisory_xact_lock реентерабелен,
    поэтому повторный захват внутри enforce_guest_limits безвреден.

    client_telegram_id определяется сервером: Guest -> NULL, Verified -> tg_user.id.
    `now` — только для детерминированных тестов окна 60 минут.
    """
    restaurant_id = tg_user.restaurant.id
    location = _resolve_location(db, tg_user, location_id)
    resolved_location_id = location.id
    digits = phone_digits(data.client_phone)
    telegram_id = None if tg_user.is_guest else tg_user.id

    if idempotency_key is not None:
        existing = _find_by_idempotency_key(db, resolved_location_id, idempotency_key)
        if existing is not None:
            return _replay_or_conflict(
                existing, digits=digits, telegram_id=telegram_id, data=data
            )

    if not location.is_reservation_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_DETAIL_RESERVATION_DISABLED,
        )

    if tg_user.is_guest:
        _acquire_guest_limit_lock(db, resolved_location_id, digits)
        if idempotency_key is not None:
            existing = _find_by_idempotency_key(db, resolved_location_id, idempotency_key)
            if existing is not None:
                return _replay_or_conflict(
                    existing, digits=digits, telegram_id=telegram_id, data=data
                )
        enforce_guest_limits(db, location_id=resolved_location_id, digits=digits, now=now)

    reservation = Reservation(
        restaurant_id=restaurant_id,
        location_id=resolved_location_id,   # S1-4 canonical
        client_name=data.client_name,
        client_phone=data.client_phone,
        client_phone_digits=digits,
        guests_count=data.guests_count,
        reservation_time=data.reservation_time,
        comment=data.comment,
        status="requested",
        client_telegram_id=telegram_id,
        idempotency_key=idempotency_key,
    )
    db.add(reservation)

    lost_idempotency_race = False
    try:
        db.commit()
        db.refresh(reservation)
    except IntegrityError as exc:
        db.rollback()
        if idempotency_key is None or not _is_idempotency_violation(exc):
            _raise_create_failed(db, exc, restaurant_id, data.client_name)
        lost_idempotency_race = True
    except Exception as exc:
        _raise_create_failed(db, exc, restaurant_id, data.client_name)

    if lost_idempotency_race:
        # Параллельный запрос с тем же (location_id, ключ) успел раньше: replay#3.
        existing = _find_by_idempotency_key(db, resolved_location_id, idempotency_key)
        if existing is None:
            logger.error(
                "Нарушение uq_reservations_idempotency без видимой строки: location_id=%s",
                resolved_location_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Ошибка при создании брони",
            )
        return _replay_or_conflict(existing, digits=digits, telegram_id=telegram_id, data=data)

    logger.info(
        "Бронь создана: reservation_id=%s restaurant_id=%s location_id=%s client=%s",
        reservation.id, restaurant_id, resolved_location_id, data.client_name,
    )
    return ReservationCreateResult(reservation=reservation, replayed=False)


def create_reservation(
    db: Session,
    *,
    tg_user: TelegramUser,
    location_id: int,
    data: ReservationCreate,
    now: datetime | None = None,
) -> Reservation:
    """
    Совместимая обёртка (контракт прежний: -> Reservation, без Idempotency-Key).

    Единственная бизнес-реализация создания — create_reservation_idempotent.
    """
    return create_reservation_idempotent(
        db,
        tg_user=tg_user,
        location_id=location_id,
        data=data,
        idempotency_key=None,
        now=now,
    ).reservation


def transition_status(
    db: Session,
    principal: Principal,
    reservation_id: int,
    target: str,
    *,
    now: datetime | None = None,
) -> Reservation:
    """
    Меняет статус брони по таблице status_transitions.RESERVATION_STATUS_TRANSITIONS.

    - бронь ищется только среди броней Restaurant principal-а (tenant isolation) и
      блокируется FOR UPDATE: два администратора не применят переход одновременно —
      второй увидит новый статус и получит 409;
    - доступ: assert_location_access (Restaurant -> Location -> Object);
    - недопустимый или повторный переход (в т.ч. из терминального статуса) -> 409;
    - lifecycle timestamp ставится сервисом в UTC и write-once (непустое значение не
      перезаписывается). Legacy 'new' ведёт себя как 'requested' (R1).
    """
    restaurant_id = principal.restaurant.id
    reservation = (
        db.query(Reservation)
        .filter(
            Reservation.id == reservation_id,
            Reservation.restaurant_id == restaurant_id,
        )
        .with_for_update()
        .first()
    )
    if reservation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_DETAIL_RESERVATION_NOT_FOUND,
        )

    assert_location_access(
        principal,
        restaurant_id=restaurant_id,
        location=db.get(Location, reservation.location_id),
        obj=reservation,
        obj_not_found_detail=_DETAIL_RESERVATION_NOT_FOUND,
    )

    old_status = reservation.status
    allowed = RESERVATION_STATUS_TRANSITIONS.get(old_status, [])
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Переход «{old_status}» → «{target}» невозможен. "
                f"Допустимые: {allowed if allowed else 'нет (финальный статус)'}"
            ),
        )

    reservation.status = target
    timestamp_field = _STATUS_TIMESTAMP_FIELDS.get(target)
    if timestamp_field is not None and getattr(reservation, timestamp_field) is None:
        setattr(reservation, timestamp_field, now or datetime.now(UTC))

    try:
        db.commit()
        db.refresh(reservation)
    except Exception as exc:
        logger.exception(
            "Ошибка при обновлении статуса брони: reservation_id=%s", reservation_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении статуса",
        ) from exc

    logger.info(
        "Статус брони изменён: reservation_id=%s %s → %s restaurant_id=%s actor=%s",
        reservation_id, old_status, target, restaurant_id, principal.actor_ref,
    )
    return reservation
