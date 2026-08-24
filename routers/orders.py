"""
routers/orders.py — Taomly Platform

Изменения v5:
  - Исправлен вызов notify_client_accepted: теперь передаёт restaurant вторым
    аргументом → устранён TypeError

Изменения v6 (Security/Billing):
  - create_order: проверяет лимит заказов по активной подписке.
    Ресторан на Free plan (100 заказов/месяц) не сможет создать 101-й.

Изменения v7 (Client History):
  - GET /my — история заказов текущего клиента (по client_telegram_id).
    Требует X-Restaurant-Id + X-Telegram-Init-Data (или гостевой режим).
    Гостевой пользователь (tg_user.id == 0) получает пустой список.
  - GET /my/{order_id} — один заказ клиента по ID (live-статус).
    Используется index.html для polling статуса после оформления заказа.

Изменения v8 (Usage Events):
  - create_order: после успешного commit пишет UsageEvent(event_type='order_created')
    в таблицу usage_events. Ошибка записи события не откатывает заказ —
    логируется предупреждение, заказ возвращается клиенту.

Изменения v9 (S1-3: orders.location_id):
  - create_order: принимает X-Location-Id header — явный источник Location.
    Location резолвится из БД и валидируется: location.restaurant_id == restaurant.id.
    Без X-Location-Id → 400 (Location должна быть explicit, не угадывается).
  - dine_in: проверяет RestaurantTable.location_id == location.id.
    Стол из другой Location того же Brand → 400 (cross-location reject).
    Стол из другого Brand → 404 (уже существующая tenant-isolation).
  - Order создаётся с location_id = location.id (canonical operational scope).
  - Legacy compat: order.restaurant_id = location.restaurant_id заполняется
    одновременно для сохранения consistency до Migration 0015.
  - Quota остаётся Brand-level (restaurant_id) — S1-8 task.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from auth import TelegramUser, get_current_restaurant_admin, get_telegram_user
from database import get_db
from models import Location, Order, OrderItem, Product, Restaurant, RestaurantTable, Subscription, SubscriptionPlan, UsageEvent, User
from schemas import OrderCreate, OrderResponse, OrderStatusUpdate
import handlers
from limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter()


# ──────────────────────────────────────────
# HELPER — PostgreSQL advisory lock (F-32: вынесен в utils.py)
# ──────────────────────────────────────────
from utils import pg_advisory_lock as _pg_advisory_lock, format_price as _fmt_price


# ──────────────────────────────────────────
# HELPER — check order quota
# ──────────────────────────────────────────
def _check_order_quota(db: Session, restaurant_id: int) -> None:
    """
    Проверяет что ресторан не превысил лимит заказов по текущему тарифному плану.

    Алгоритм:
      1. Загружает активную подписку ресторана.
      2. Если подписки нет — считает ресторан на Free плане.
      3. Если orders_per_month == -1 — безлимит, проверка не нужна.
      4. Считает заказы за текущий месяц (статус != 'cancelled').
      5. Если лимит превышен — возвращает HTTP 402 с понятным сообщением.

    Вызывается в create_order до любой записи в БД.
    """
    sub = (
        db.query(Subscription)
        .filter(
            Subscription.restaurant_id == restaurant_id,
            Subscription.is_active == True,
        )
        .order_by(Subscription.started_at.desc())
        .first()
    )

    if sub is None:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "Free").first()
        if plan is None:
            return
        orders_limit = plan.orders_per_month
    else:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
        if plan is None:
            return
        orders_limit = plan.orders_per_month

    if orders_limit == -1:
        return

    # Advisory lock: quota check+insert is atomic per restaurant.
    # Two concurrent create_order calls for the same restaurant cannot
    # both pass the quota check. Namespace: restaurant_id directly.
    if not _pg_advisory_lock(db, restaurant_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много одновременных запросов. Попробуйте ещё раз.",
        )

    now = datetime.now(tz=timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM orders "
            "WHERE restaurant_id = :rid "
            "  AND created_at >= :start "
            "  AND status != 'cancelled'"
        ),
        {"rid": restaurant_id, "start": month_start},
    ).fetchone()

    orders_used = int(row.cnt) if row else 0

    if orders_used >= orders_limit:
        logger.warning(
            "Квота заказов исчерпана: restaurant_id=%s used=%s limit=%s plan=%s",
            restaurant_id, orders_used, orders_limit, plan.name,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Достигнут лимит заказов тарифного плана «{plan.name}»: "
                f"{orders_used}/{orders_limit} в этом месяце. "
                "Обратитесь к вашему агентству для смены тарифа."
            ),
        )


# Статусная машина заказов.
# Заказ создаётся сразу со статусом "accepted" (авто-подтверждение).
# Статус "new" оставлен в машине для совместимости на случай если
# владелец захочет добавить ручное подтверждение заказов в будущем.
from status_transitions import ORDER_STATUS_TRANSITIONS as VALID_STATUS_TRANSITIONS


# ──────────────────────────────────────────
# POST / — создать заказ (клиент Mini App)
# ──────────────────────────────────────────
@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def create_order(
    request: Request,
    data: OrderCreate,
    background_tasks: BackgroundTasks,
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
    x_location_id: int = Header(..., alias="X-Location-Id"),
):
    """
    Создаёт новый заказ.

    Источники данных (все верифицированы до роутера):
      restaurant         → tg_user.restaurant    (загружен в get_telegram_user)
      restaurant_id      → tg_user.restaurant_id (из X-Restaurant-Id)
      location_id        → X-Location-Id header  (explicit, не угадывается)
      client_telegram_id → tg_user.id            (из initData, HMAC-SHA256)
      total_amount       → вычислен из цен БД    (нельзя подменить)
      quantity           → проверен Pydantic ge=1

    S1-3: X-Location-Id обязателен. Location резолвится из БД и валидируется:
      location.restaurant_id == restaurant.id — защита от cross-brand injection.
      location.is_active == True — деактивированная Location не принимает заказы.
    """
    restaurant = tg_user.restaurant

    # ── S1-3: Resolve and validate Location ───────────────────────────────
    #
    # X-Location-Id — explicit источник Location. Не угадываем по restaurant_id,
    # не берём «первую» Location. Клиент обязан указать конкретную Location.
    #
    # Двойная проверка:
    #   1. location.restaurant_id == restaurant.id → защита от cross-brand injection
    #      (клиент ресторана A не может создать заказ в Location бренда B)
    #   2. location.is_active — деактивированная Location не принимает заказы
    location = db.query(Location).filter(
        Location.id == x_location_id,
        Location.restaurant_id == restaurant.id,
        Location.is_active == True,
    ).first()

    if not location:
        # Намеренно не различаем "не найдена" и "чужая" — одинаковый 404
        # (информационная безопасность: не раскрываем существование чужих Location)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location не найдена или недоступна для этого ресторана",
        )

    # Проверяем квоту заказов по тарифному плану до любой записи в БД.
    # Quota остаётся Brand-level (restaurant_id) — S1-8.
    _check_order_quota(db, restaurant.id)

    if data.order_type == "dine_in":
        if not data.table_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Для заказа в зале необходимо указать номер стола",
            )
        # S1-3: dine_in validation — двойная проверка стола:
        #   1. RestaurantTable.restaurant_id == restaurant.id  (Brand isolation)
        #   2. RestaurantTable.location_id == location.id     (Location isolation)
        #
        # Сценарии отказа:
        #   - Стол другого Brand → 404 (restaurant_id mismatch)
        #   - Стол того же Brand, другой Location → 400 (location_id mismatch)
        #     Это ключевой новый check S1-3: Brand A / Location A1 не может
        #     принять заказ за столом Brand A / Location A2.
        table = db.query(RestaurantTable).filter(
            RestaurantTable.id == data.table_id,
            RestaurantTable.restaurant_id == restaurant.id,
        ).first()
        if not table:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Стол не найден в этом ресторане",
            )
        if table.location_id != location.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Стол принадлежит другой локации. "
                    "Убедитесь, что X-Location-Id соответствует локации стола."
                ),
            )

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

    # S1-7: min_order_amount и currency берутся из Location (source of truth).
    # location уже resolved выше и tenant-изолирован.
    _min_order = location.min_order_amount or 0
    _cur = location.currency or "UZS"

    # Проверка минимальной суммы заказа для доставки
    if (
        data.order_type == "delivery"
        and _min_order
        and total < _min_order
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Минимальная сумма заказа для доставки: "
                f"{_fmt_price(_min_order, _cur)}. "
                f"Ваш заказ: {_fmt_price(total, _cur)}."
            ),
        )

    # Ищем User запись по telegram_id чтобы заполнить client_id (FK на users.id).
    # Гостевой пользователь (tg_user.id == 0) не имеет записи в users — client_id = NULL.
    client_db_id: int | None = None
    if tg_user.id != 0:
        user_row = db.query(User.id).filter(
            User.telegram_id == tg_user.id,
            User.restaurant_id == restaurant.id,
        ).first()
        if user_row:
            client_db_id = user_row[0]

    # S1-3: Order создаётся с location_id (canonical operational scope).
    # Legacy compat: restaurant_id = location.restaurant_id заполняется
    # одновременно для consistency до Migration 0015.
    # Invariant: order.restaurant_id == order.location.restaurant_id — всегда.
    order = Order(
        restaurant_id=location.restaurant_id,   # legacy, == restaurant.id
        location_id=location.id,                # S1-3 canonical
        client_id=client_db_id,
        client_telegram_id=tg_user.id,
        client_name=data.client_name or tg_user.display_name,
        client_phone=data.client_phone,
        order_type=data.order_type,
        address=data.address,
        table_id=data.table_id,
        comment=data.comment,
        total_amount=total,
        status="accepted",
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
            restaurant.id, tg_user.id,
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

    # Записываем событие использования для биллинга/аудита.
    # Ошибка записи события НЕ откатывает уже созданный заказ —
    # квота всё равно пересчитывается из COUNT(orders) при следующем запросе.
    try:
        db.add(UsageEvent(
            restaurant_id=restaurant.id,
            location_id=location.id,   # S1-4: location в scope (resolved выше)
            event_type="order_created",
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Не удалось записать UsageEvent order_created: order_id=%s restaurant_id=%s location_id=%s",
            order_with_items.id, restaurant.id, location.id,
        )

    background_tasks.add_task(
        handlers.notify_new_order,
        order_with_items,
        order_with_items.items,
        restaurant,
        location,   # S1-7: currency берётся из Location
    )
    background_tasks.add_task(
        handlers.notify_client_accepted,
        order_with_items,
        restaurant,
    )

    logger.info(
        "Заказ создан: order_id=%s restaurant_id=%s tg_user=%s total=%s",
        order_with_items.id, restaurant.id, tg_user.id, total,
    )
    return order_with_items


# ──────────────────────────────────────────
# GET /my — история заказов клиента
# ──────────────────────────────────────────
@router.get("/my", response_model=List[OrderResponse])
@limiter.limit("30/minute")
def get_my_orders(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
):
    """
    Возвращает историю заказов текущего клиента в данном ресторане.

    Идентификация клиента — по client_telegram_id из верифицированной initData.
    Гостевой пользователь (tg_user.id == 0, браузер без Telegram) — пустой список.

    Tenant-изоляция: фильтр по restaurant_id из X-Restaurant-Id гарантирует
    что клиент видит только свои заказы в текущем ресторане.
    """
    if tg_user.id == 0:
        return []

    orders = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(
            Order.restaurant_id == tg_user.restaurant_id,
            Order.client_telegram_id == tg_user.id,
        )
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )
    return orders


# ──────────────────────────────────────────
# GET /my/{order_id} — один заказ клиента (live-статус)
# ──────────────────────────────────────────
@router.get("/my/{order_id}", response_model=OrderResponse)
@limiter.limit("60/minute")
def get_my_order(
    request: Request,
    order_id: int,
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
):
    """
    Возвращает один заказ клиента по ID.

    Используется index.html для polling статуса после оформления заказа:
      каждые 5 секунд GET /api/orders/my/{order_id} → обновляет UI.

    Безопасность:
      - Гостевой пользователь (id=0) не может видеть чужие заказы → 404.
      - Фильтр по client_telegram_id + restaurant_id — IDOR невозможен.
    """
    if tg_user.id == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Заказ не найден",
        )

    order = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(
            Order.id == order_id,
            Order.restaurant_id == tg_user.restaurant_id,
            Order.client_telegram_id == tg_user.id,
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
# GET /restaurant/{restaurant_id} — список заказов (админка)
# ──────────────────────────────────────────
@router.get("/restaurant/{restaurant_id}", response_model=List[OrderResponse])
def get_restaurant_orders(
    restaurant_id: int,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    # S1-5: необязательный фильтр по Location.
    # Без параметра → Brand-level (все заказы ресторана, backward compat).
    # С параметром → только заказы указанной Location (tenant-изолировано).
    location_id: Optional[int] = Query(None, alias="location_id"),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает заказы ресторана.
    Tenant-изоляция: restaurant_id из URL проверяется против токена JWT.
    S1-5: опциональный ?location_id=<id> — фильтр по Location.
      Без location_id → все заказы ресторана (Brand-level, backward compat).
      С location_id   → только заказы указанной Location.
      location_id чужого ресторана → 404.
    """
    if restaurant.id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к заказам этого ресторана",
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
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.restaurant_id == restaurant_id)
    )

    # S1-5: применяем фильтр только если явно передан
    if location_id is not None:
        query = query.filter(Order.location_id == location_id)

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

    Foundation Gate P0-1: запрос разделён на два шага.

    Причина: PostgreSQL запрещает FOR UPDATE на nullable side of outer join.
    joinedload(Order.items) генерирует LEFT OUTER JOIN order_items — несовместимо
    с with_for_update() на уровне всего запроса. На SQLite баг не проявлялся,
    на PostgreSQL — crash HTTP 500 при любом PATCH /{order_id}/status.

    Решение (два шага):
      1. SELECT orders WHERE id + restaurant_id FOR UPDATE — только таблица orders,
         без join. Захватываем row-level lock на запись заказа.
      2. db.refresh(order) после lock — подгружает items отдельным SELECT
         (lazy="select" на relationship, см. models.py:402).
    Бизнес-логика не изменена.
    """
    # Шаг 1: блокируем строку без JOIN. Tenant isolation: restaurant_id filter.
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.restaurant_id == restaurant.id,
        )
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Заказ не найден",
        )

    # Шаг 2: подгружаем items после блокировки (отдельный SELECT, без join).
    db.refresh(order)

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

    _status_notify = {
        "accepted":           handlers.notify_client_accepted,
        "preparing":          handlers.notify_client_preparing,
        "ready_for_delivery": handlers.notify_client_ready,
        "delivering":         handlers.notify_client_delivering,
        "completed":          handlers.notify_client_completed,
    }
    if data.status in _status_notify:
        background_tasks.add_task(_status_notify[data.status], order, restaurant)
    elif data.status == "cancelled":
        background_tasks.add_task(
            handlers.notify_client_cancelled,
            order,
            restaurant,
            "",
        )

    return order
