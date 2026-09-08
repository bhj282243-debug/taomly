"""
modules/cart/router.py — Taomly Platform
Phase 6: Cart Engine HTTP endpoints.

Endpoints:
  GET    /api/cart                 — get or return empty cart
  POST   /api/cart/items           — add item (blocking, server-authoritative)
  PATCH  /api/cart/items/{item_id} — update quantity (optimistic-safe)
  DELETE /api/cart/items/{item_id} — remove item
  DELETE /api/cart                 — clear cart

HTTP concerns only. Business logic in service.py.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from modules.cart.dependencies import CartContext, get_cart_context, get_cart_context_read
from modules.cart.schemas import (
    AddItemRequest,
    CartResponse,
    UpdateQuantityRequest,
)
from modules.cart import service

router = APIRouter()


# ──────────────────────────────────────────
# GET /api/cart
# ──────────────────────────────────────────
@router.get("", response_model=CartResponse)
def get_cart(
    ctx: CartContext = Depends(get_cart_context_read),
    db: Session = Depends(get_db),
):
    """
    Return the current active cart for this session+restaurant.
    If no cart exists, return an empty cart structure (no 404).
    Server Cart is authoritative — used by frontend to hydrate state on page load.
    """
    cart = service.get_cart(db, ctx.restaurant_id, ctx.session_id)
    if cart is None:
        # Return empty cart representation (no DB record created)
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


# ──────────────────────────────────────────
# POST /api/cart/items
# ──────────────────────────────────────────
@router.post("/items", response_model=CartResponse, status_code=status.HTTP_200_OK)
def add_item(
    body:  AddItemRequest,
    ctx:   CartContext = Depends(get_cart_context),
    db:    Session     = Depends(get_db),
):
    """
    Add an item to the cart.

    - Prices are computed server-side from DB. Client-supplied prices are ignored.
    - Duplicate modifier_option_ids → HTTP 400.
    - Currency mismatch with existing cart → HTTP 409.
    - Product/variant/modifier ownership validated against authoritative restaurant.
    - Concurrent identical adds are handled atomically via INSERT ON CONFLICT DO UPDATE.
    - unit_price is a snapshot: not updated on subsequent identical adds.
    """
    cart = service.get_or_create_cart(
        db=db,
        restaurant_id=ctx.restaurant_id,
        session_id=ctx.session_id,
        telegram_id=ctx.telegram_id,
        currency=ctx.location.currency,
    )
    cart = service.add_item(
        db=db,
        cart=cart,
        product_id=body.product_id,
        variant_id=body.variant_id,
        modifier_option_ids=body.modifier_option_ids,
        notes=body.notes,
        quantity=body.quantity,
        location=ctx.location,
    )
    return service.build_cart_response(cart, db)


# ──────────────────────────────────────────
# PATCH /api/cart/items/{item_id}
# ──────────────────────────────────────────
@router.patch("/items/{item_id}", response_model=CartResponse)
def update_item(
    item_id: int,
    body:    UpdateQuantityRequest,
    ctx:     CartContext = Depends(get_cart_context_read),
    db:      Session     = Depends(get_db),
):
    """
    Update quantity of a cart item.
    unit_price is never changed (snapshot semantics).
    line_total = unit_price × new_quantity, computed server-side.
    Uses SELECT FOR UPDATE for safe concurrent updates.
    """
    cart = service.get_cart(db, ctx.restaurant_id, ctx.session_id)
    if cart is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart not found.",
        )
    cart = service.update_item_quantity(
        db=db,
        cart=cart,
        item_id=item_id,
        new_quantity=body.quantity,
    )
    return service.build_cart_response(cart, db)


# ──────────────────────────────────────────
# DELETE /api/cart/items/{item_id}
# ──────────────────────────────────────────
@router.delete("/items/{item_id}", response_model=CartResponse)
def remove_item(
    item_id: int,
    ctx:     CartContext = Depends(get_cart_context_read),
    db:      Session     = Depends(get_db),
):
    """
    Remove a single item from the cart.
    Returns updated cart. Cart itself is not deleted.
    """
    cart = service.get_cart(db, ctx.restaurant_id, ctx.session_id)
    if cart is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart not found.",
        )
    cart = service.remove_item(db=db, cart=cart, item_id=item_id)
    return service.build_cart_response(cart, db)


# ──────────────────────────────────────────
# DELETE /api/cart
# ──────────────────────────────────────────
@router.delete("", status_code=status.HTTP_200_OK)
def clear_cart(
    ctx: CartContext = Depends(get_cart_context_read),
    db:  Session     = Depends(get_db),
):
    """
    Remove all items from the cart.
    Cart record itself remains with status='active'.
    Returns {"ok": true}.
    """
    cart = service.get_cart(db, ctx.restaurant_id, ctx.session_id)
    if cart is not None:
        service.clear_cart(db=db, cart=cart)
    return {"ok": True}
