"""
routers/waiter_calls.py — Taomly Platform

Изменения v2:
  - WaiterCallCreate: restaurant_id убран из схемы, берётся из TelegramUser
  - Race condition: проверка существующего вызова защищена SELECT FOR UPDATE —
    конкурентные запросы с одного стола не создадут два активных вызова
  - GET /restaurant/{restaurant_id}: добавлен limit=100 по умолчанию
  - PATCH /{call_id}/status: tenant-изоляция + VALID_STATUS_TRANSITIONS
  - Логирование через logger.exception с контекстом
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import TelegramUser, get_current_restaurant_admin, get_telegram_user
from database import get_db
from models import Restaurant, RestaurantTable, WaiterCall
from schemas import WaiterCallCreate, WaiterCallResponse, WaiterCallStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "active":    ["accepted", "cancelled"],
    "accepted":  ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}


# ──────────────────────────────────────────
# POST / — создать вызов официанта (клиент Mini App)
# ──────────────────────────────────────────
@router.post("/", response_model=WaiterCallResponse, status_code=status.HTTP_201_CREATED)
def create_waiter_call(
    data: WaiterCallCreate,
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
):
    """
    Создаёт вызов официанта.

    restaurant берётся из TelegramUser — клиент не передаёт restaurant_id.

    Защита от Race Condition:
      SELECT FOR UPDATE блокирует строки с активными вызовами для этого стола
      до завершения транзакции. Если два запроса придут одновременно —
      второй будет ждать пока первый завершит commit, и затем найдёт
      уже существующий активный вызов и вернёт 400.
    """
    restaurant = tg_user.restaurant

    # Проверяем что стол принадлежит этому ресторану
    table = db.query(RestaurantTable).filter(
        RestaurantTable.id == data.table_id,
        RestaurantTable.restaurant_id == restaurant.id,
    ).first()
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Стол не найден в этом ресторане",
        )

    # SELECT FOR UPDATE — блокируем строки на время транзакции.
    # Конкурентный запрос будет ждать здесь пока мы не сделаем commit/rollback.
    existing = (
        db.execute(
            select(WaiterCall)
            .where(
                WaiterCall.restaurant_id == restaurant.id,
                WaiterCall.table_id == data.table_id,
                WaiterCall.status.in_(["active", "accepted"]),
            )
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для этого стола уже есть активный вызов",
        )

    call = WaiterCall(
        restaurant_id=restaurant.id,
        table_id=data.table_id,
        status="active",
    )
    db.add(call)

    try:
        db.commit()
        db.refresh(call)
    except Exception:
        logger.exception(
            "Ошибка при создании вызова официанта: restaurant_id=%s table_id=%s",
            restaurant.id,
            data.table_id,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании вызова",
        )

    logger.info(
        "Вызов официанта создан: call_id=%s table_id=%s restaurant_id=%s",
        call.id, data.table_id, restaurant.id,
    )
    return call


# ──────────────────────────────────────────
# GET /restaurant/{restaurant_id} — список вызовов (админка)
# ──────────────────────────────────────────
@router.get("/restaurant/{restaurant_id}", response_model=List[WaiterCallResponse])
def get_waiter_calls(
    restaurant_id: int,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает вызовы официанта для ресторана.
    Tenant-изоляция: restaurant_id из URL проверяется против токена JWT.
    Лимит по умолчанию 100 — защита от перегрузки при большом количестве записей.
    """
    if restaurant.id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к данным этого ресторана",
        )

    query = db.query(WaiterCall).filter(
        WaiterCall.restaurant_id == restaurant_id,
    )

    if status_filter:
        valid_statuses = list(VALID_STATUS_TRANSITIONS.keys())
        if status_filter not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недопустимый статус. Допустимые: {valid_statuses}",
            )
        query = query.filter(WaiterCall.status == status_filter)

    return query.order_by(WaiterCall.created_at.desc()).limit(limit).all()


# ──────────────────────────────────────────
# PATCH /{call_id}/status — сменить статус (админка)
# ──────────────────────────────────────────
@router.patch("/{call_id}/status", response_model=WaiterCallResponse)
def update_status(
    call_id: int,
    data: WaiterCallStatusUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Меняет статус вызова официанта.
    Tenant-изоляция: вызов ищется только среди вызовов ресторана из токена.
    Проверяется допустимость перехода статуса.
    """
    call = db.query(WaiterCall).filter(
        WaiterCall.id == call_id,
        WaiterCall.restaurant_id == restaurant.id,
    ).first()

    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Вызов не найден",
        )

    allowed = VALID_STATUS_TRANSITIONS.get(call.status, [])
    if data.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Переход «{call.status}» → «{data.status}» невозможен. "
                f"Допустимые: {allowed if allowed else 'нет (финальный статус)'}"
            ),
        )

    old_status = call.status
    call.status = data.status

    try:
        db.commit()
        db.refresh(call)
    except Exception:
        logger.exception(
            "Ошибка при обновлении статуса вызова: call_id=%s", call_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении статуса",
        )

    logger.info(
        "Статус вызова изменён: call_id=%s %s → %s restaurant_id=%s",
        call_id, old_status, data.status, restaurant.id,
    )
    return call
