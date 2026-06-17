from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List

from database import get_db
from models import Order, OrderItem, Product, Restaurant
from schemas import OrderCreate, OrderResponse, OrderStatusUpdate

router = APIRouter()

VALID_STATUS_TRANSITIONS = {
    "new":        ["accepted", "cancelled"],
    "accepted":   ["preparing", "cancelled"],
    "preparing":  ["delivering", "cancelled"],
    "delivering": ["completed"],
    "completed":  [],
    "cancelled":  [],
}


# ──────────────────────────────────────────
# POST /api/orders/  — создать заказ
# ──────────────────────────────────────────
@router.post("/", response_model=OrderResponse)
def create_order(data: OrderCreate, db: Session = Depends(get_db)):
    # Проверяем что заказ не пустой
    if not data.items:
        raise HTTPException(status_code=400, detail="Заказ не содержит товаров")

    # Проверяем ресторан
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == data.restaurant_id,
        Restaurant.is_active == True
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    # Проверяем товары и считаем сумму
    total = 0
    order_items_data = []

    for item in data.items:
        product = db.query(Product).filter(
            Product.id == item.product_id,
            Product.restaurant_id == data.restaurant_id
        ).first()

        if not product:
            raise HTTPException(
                status_code=404,
                detail=f"Продукт {item.product_id} не найден"
            )
        if not product.is_available:
            raise HTTPException(
                status_code=400,
                detail=f"Продукт '{product.name}' недоступен"
            )

        total += product.price * item.quantity
        order_items_data.append({
            "product_id": product.id,
            "name": product.name,
            "price": product.price,
            "quantity": item.quantity,
        })

    # Создаём заказ
    order = Order(
        restaurant_id=data.restaurant_id,
        client_telegram_id=data.client_telegram_id,
        client_name=data.client_name,
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
    db.flush()  # получаем order.id до commit

    # Создаём позиции заказа
    for item_data in order_items_data:
        db.add(OrderItem(order_id=order.id, **item_data))

    try:
        db.commit()
        db.refresh(order)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка при сохранении заказа")

    return order


# ──────────────────────────────────────────
# GET /api/orders/{order_id}  — получить заказ
# ──────────────────────────────────────────
@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return order


# ──────────────────────────────────────────
# GET /api/orders/restaurant/{restaurant_id}  — список заказов ресторана
# ──────────────────────────────────────────
@router.get("/restaurant/{restaurant_id}", response_model=List[OrderResponse])
def get_restaurant_orders(restaurant_id: int, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    orders = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.restaurant_id == restaurant_id)
        .order_by(Order.created_at.desc())
        .all()
    )
    return orders


# ──────────────────────────────────────────
# PATCH /api/orders/{order_id}/status  — изменить статус
# ──────────────────────────────────────────
@router.patch("/{order_id}/status", response_model=OrderResponse)
def update_order_status(
    order_id: int,
    data: OrderStatusUpdate,
    db: Session = Depends(get_db)
):
    order = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    allowed = VALID_STATUS_TRANSITIONS.get(order.status, [])
    if data.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Переход {order.status} → {data.status} невозможен"
        )

    order.status = data.status
    db.commit()
    db.refresh(order)
    return order
