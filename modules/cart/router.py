"""
modules/cart/router.py — Taomly Platform
Phase 6: Cart Engine HTTP endpoints.
Phase 7: Added POST /api/cart/checkout.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

import handlers
from database import get_db
from models.orders import Order, OrderItem
from modules.cart.dependencies import CartContext, get_cart_context, get_cart_context_read
from modules.cart.schemas import (
    AddItemRequest, CartResponse, CheckoutRequest, UpdateQuantityRequest,
)
from modules.cart import service
from schemas.orders import OrderResponse

router = APIRouter()


@router.get("", response_model=CartResponse)
def get_cart(
    ctx: CartContext = Depends(get_cart_context_read),
    db: Session = Depends(get_db),
):
    cart = service.get_cart(db, ctx.restaurant_id, ctx.session_id)
    if cart is None:
        return CartResponse(
            cart_id=0,
            restaurant_id=ctx.restaurant_id,
            currency=ctx.location.currency,
            status="active",
            items=[],
            subtotal=0,
            item_count=0,
        )
    return service.build_cart_response(cart, db)


@router.post("/items", response_model=CartResponse, status_code=status.HTTP_200_OK)
def add_item(
    body: AddItemRequest,
    ctx:  CartContext = Depends(get_cart_context),
    db:   Session     = Depends(get_db),
):
    cart = service.get_or_create_cart(
        db=db,
        restaurant_id=ctx.restaurant_id,
        session_id=ctx.session_id,
        telegram_id=ctx.telegram_id,
        currency=ctx.location.currency,
    )
    cart = service.add_item(
        db=db, cart=cart,
        product_id=body.product_id,
        variant_id=body.variant_id,
        modifier_option_ids=body.modifier_option_ids,
        notes=body.notes,
        quantity=body.quantity,
        location=ctx.location,
    )
    return service.build_cart_response(cart, db)


@router.patch("/items/{item_id}", response_model=CartResponse)
def update_item(
    item_id: int,
    body:    UpdateQuantityRequest,
    ctx:     CartContext = Depends(get_cart_context_read),
    db:      Session     = Depends(get_db),
):
    cart = service.get_cart(db, ctx.restaurant_id, ctx.session_id)
    if cart is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found.")
    cart = service.update_item_quantity(db=db, cart=cart, item_id=item_id, new_quantity=body.quantity)
    return service.build_cart_response(cart, db)


@router.delete("/items/{item_id}", response_model=CartResponse)
def remove_item(
    item_id: int,
    ctx:     CartContext = Depends(get_cart_context_read),
    db:      Session     = Depends(get_db),
):
    cart = service.get_cart(db, ctx.restaurant_id, ctx.session_id)
    if cart is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found.")
    cart = service.remove_item(db=db, cart=cart, item_id=item_id)
    return service.build_cart_response(cart, db)


@router.delete("", status_code=status.HTTP_200_OK)
def clear_cart(
    ctx: CartContext = Depends(get_cart_context_read),
    db:  Session     = Depends(get_db),
):
    cart = service.get_cart(db, ctx.restaurant_id, ctx.session_id)
    if cart is not None:
        service.clear_cart(db=db, cart=cart)
    return {"ok": True}


@router.post("/checkout", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def checkout(
    body:             CheckoutRequest,
    background_tasks: BackgroundTasks,
    ctx:              CartContext = Depends(get_cart_context),
    db:               Session     = Depends(get_db),
):
    """
    POST /api/cart/checkout — Convert active Cart into immutable Order.

    - Cart locked FOR UPDATE NOWAIT (concurrent checkout → 409)
    - Availability validated fresh from DB
    - Prices from CartItem.unit_price (snapshot, never re-read from menu)
    - Currency from Cart.currency = Location.currency at cart creation
    - Notifications sent AFTER commit via BackgroundTasks
    """
    order = service.checkout_cart(
        db=db,
        session_id=ctx.session_id,
        restaurant_id=ctx.restaurant_id,
        location=ctx.location,
        telegram_id=ctx.telegram_id,
        order_type=body.order_type,
        client_name=body.client_name,
        client_phone=body.client_phone,
        address=body.address,
        table_id=body.table_id,
        comment=body.comment,
        idempotency_key=body.idempotency_key,
        display_name=ctx.tg_user.display_name,
    )

    # Load with items for response and notifications (after commit)
    order_with_items = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.selected_modifiers))
        .filter(Order.id == order.id)
        .first()
    )

    background_tasks.add_task(
        handlers.notify_new_order,
        order_with_items,
        order_with_items.items,
        ctx.tg_user.restaurant,
        ctx.location,
    )
    background_tasks.add_task(
        handlers.notify_client_accepted,
        order_with_items,
        ctx.tg_user.restaurant,
        ctx.location,
    )

    return order_with_items
