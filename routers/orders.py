"""
routers/orders.py — Taomly Platform

Изменения v5:
  - Исправлен вызов notify_client_accepted: теперь передаёт restaurant вторым
    аргументом → устранён TypeError
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session, joinedload

from auth import TelegramUser, get_current_restaurant_admin, get_telegram_user
from database import get_db
from models import Order, OrderItem, Product, Restaurant, RestaurantTable
from schemas import OrderCreate, OrderResponse, OrderStatusUpdate
import handlers
from limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "new":                ["accepted", "cancelled"],
    "accepted":           ["preparing", "cancelled"],
    "preparing":          ["ready_for_delivery", "cancelled"],
    "ready_for_delivery": ["delivering", "cancelled"],
    "delivering":         ["completed"],
    "completed":          [],
    "cancelled":          [],
}


# ──────────────────────────────────────────
# POST / — создать заказ (клиент Mini App)
# ──────────────────────────────────────────
@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")  # защита от flood заказов
def create_order(
    request: Request,
    data: OrderCreate,
    background_tasks: BackgroundTasks,
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
):
    """
    Создаёт новый заказ.

    Источники данных (все верифицированы до роутера):
      restaurant        → tg_user.restaurant    (загружен в get_telegram_user)
      restaurant_id     → tg_user.restaurant_id (из X-Restaurant-Id)
      client_telegram_id → tg_user.id           (из initData, HMAC-SHA256)
      total_amount      → вычислен из цен БД    (нельзя подменить)
      quantity          → проверен Pydantic ge=1
    """
    restaurant = tg_user.restaurant

    # Проверка стола для dine_in
    if data.order_type == "dine_in":
        if not data.table_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Для заказа в зале необходимо указать номер стола",
            )
        table = db.query(RestaurantTable).filter(
            RestaurantTable.id == data.table_id,
            RestaurantTable.restaurant_id == restaurant.id,
        ).first()
        if not table:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Стол не найден в этом ресторане",
            )

    # Проверяем продукты и считаем сумму на сервере
    total = 0
    order_items_data: list[dict] = []

    for item in data.items:
        product = db.query(Product).filter(
            Product.id == item.product_id,
            Product.restaurant_id == restaurant.id,
        ).first()

        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Продукт {item.product_id} не найден в меню ресторана",
            )
        if not product.is_available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Продукт «{product.name}» сейчас недоступен",
            )

        total += product.price * item.quantity
        order_items_data.append({
            "product_id": product.id,
            "name":       product.name,
            "price":      product.price,
            "quantity":   item.quantity,
        })

    order = Order(
        restaurant_id=restaurant.id,
        client_telegram_id=tg_user.id,
        client_name=data.client_name or tg_user.display_name,
        client_phone=data.client_phone,
        order_type=data.order_type,
        address=data.address,
        location_lat=data.location_lat,
        location_lng=data.location_lng,
        table_id=data.table_id,
        comment=data.comment,
        total_amount=total,
        status="new",
    )
    db.add(order)
    db.flush()

    for item_data in order_items_data:
        db.add(OrderItem(order_id=order.id, **item_data))

    try:
        db.commit()
    except Exception:
        logger.exception(
            "Ошибка при сохранении заказа: restaurant_id=%s tg_user=%s",
            restaurant.id,
            tg_user.id,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при сохранении заказа. Попробуйте ещё раз.",
        )

    order_with_items = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order.id)
        .first()
    )

    background_tasks.add_task(
        handlers.notify_new_order,
        order_with_items,
        order_with_items.items,
        restaurant,
    )

    logger.info(
        "Заказ создан: order_id=%s restaurant_id=%s tg_user=%s total=%s",
        order_with_items.id, restaurant.id, tg_user.id, total,
    )
    return order_with_items


# ──────────────────────────────────────────
# GET /restaurant/{restaurant_id} — список заказов (админка)
# ──────────────────────────────────────────
@router.get("/restaurant/{restaurant_id}", response_model=List[OrderResponse])
def get_restaurant_orders(
    restaurant_id: int,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает заказы ресторана.
    Tenant-изоляция: restaurant_id из URL проверяется против токена JWT.
    """
    if restaurant.id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к заказам этого ресторана",
        )

    query = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.restaurant_id == restaurant_id)
    )

    if status_filter:
        valid_statuses = list(VALID_STATUS_TRANSITIONS.keys())
        if status_filter not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недопустимый статус. Допустимые: {valid_statuses}",
            )
        query = query.filter(Order.status == status_filter)

    return query.order_by(Order.created_at.desc()).limit(limit).all()


# ──────────────────────────────────────────
# GET /{order_id} — один заказ (админка)
# ──────────────────────────────────────────
@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает заказ по ID.
    Tenant-изоляция: фильтр по restaurant_id из токена — IDOR невозможен.
    """
    order = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(
            Order.id == order_id,
            Order.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Заказ не найден",
        )
    return order


# ──────────────────────────────────────────
# PATCH /{order_id}/status — сменить статус (админка)
# ──────────────────────────────────────────
@router.patch("/{order_id}/status", response_model=OrderResponse)
def update_order_status(
    order_id: int,
    data: OrderStatusUpdate,
    background_tasks: BackgroundTasks,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Меняет статус заказа.
    Tenant-изоляция + проверка допустимости перехода.
    """
    order = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(
            Order.id == order_id,
            Order.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Заказ не найден",
        )

    allowed = VALID_STATUS_TRANSITIONS.get(order.status, [])
    if data.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Переход «{order.status}» → «{data.status}» невозможен. "
                f"Допустимые: {allowed if allowed else 'нет (финальный статус)'}"
            ),
        )

    old_status = order.status
    order.status = data.status

    try:
        db.commit()
        db.refresh(order)
    except Exception:
        logger.exception("Ошибка при обновлении статуса заказа: order_id=%s", order_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении статуса",
        )

    logger.info(
        "Статус заказа изменён: order_id=%s %s → %s restaurant_id=%s",
        order_id, old_status, data.status, restaurant.id,
    )

    # Уведомления клиенту при каждой смене статуса.
    # Все вызовы через BackgroundTasks — не блокируют HTTP-ответ.
    _status_notify = {
        "accepted":          handlers.notify_client_accepted,
        "preparing":         handlers.notify_client_preparing,
        "ready_for_delivery": handlers.notify_client_ready,
        "delivering":        handlers.notify_client_delivering,
        "completed":         handlers.notify_client_completed,
    }
    if data.status in _status_notify:
        background_tasks.add_task(_status_notify[data.status], order, restaurant)
    elif data.status == "cancelled":
        background_tasks.add_task(
            handlers.notify_client_cancelled,
            order,
            restaurant,
            data.comment or "",
        )

    return order
