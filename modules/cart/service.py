"""
modules/cart/service.py — Taomly Platform
Phase 6: Cart Engine business logic.
Phase 7: Cart mutation serialization via Cart row lock + checkout_cart().
Phase 13: Added min_order_amount backend enforcement + web_order_token_hash generation.

PHASE 7 — Cart Mutation Serialization (ADR-P7-LOCK):
All Cart mutations (add_item, update_item_quantity, remove_item, clear_cart)
and checkout_cart serialize through SELECT Cart FOR UPDATE at transaction start.

checkout uses FOR UPDATE NOWAIT (immediate 409).
Mutations use regular FOR UPDATE (short wait OK).

PostgreSQL NOWAIT failure → sqlalchemy.exc.OperationalError pgcode '55P03' → HTTP 409.
"""

# ruff: noqa: I001
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, joinedload

from models import Location, ModifierGroup, Product, ProductVariant
from models.delivery_zones import DeliveryZone
from models.operations import RestaurantTable
from models.orders import Order, OrderItem, OrderItemModifier
from modules.cart.models import Cart, CartItem, CartItemModifier
from modules.cart.schemas import CartItemModifierResponse, CartItemResponse, CartResponse
from utils import format_price as _fmt_price, is_within_schedule

logger = logging.getLogger(__name__)

_EMPTY_MODIFIERS_HASH = hashlib.sha256(b"").hexdigest()
_PG_LOCK_NOT_AVAILABLE = "55P03"

# Phase 14: Scheduled order validation constants (OD-01: bounds-only, no working-hours check).
_SCHEDULED_MIN_ADVANCE_MINUTES: int = 30   # minimum lead time
_SCHEDULED_MAX_HORIZON_DAYS: int    = 7    # maximum scheduling horizon

# Phase 14: Scheduled order activation constants (BLOCK-02 resolution, Option A).
# NULL preparation_time_minutes fallback = 0 (documented: migration 0028 comment).
SCHEDULED_ACTIVATION_BUFFER_MINUTES: int = 5


# ── PHASE 14: SHARED DELIVERY FEE HELPER ──────────────────────────
# Used by both checkout paths: modules/cart/service.py and routers/orders.py.
# Server-authoritative: client cannot supply fee or subtotal.

def resolve_delivery_fee(
    db: Session,
    location: Location,
    order_type: str,
    zone_id: Optional[int],
) -> tuple[int, Optional[int]]:
    """
    Returns (delivery_fee, resolved_zone_id).

    For non-delivery orders: always (0, None).
    For delivery:
      - zone_id provided and valid → (zone.fee, zone.id)
      - zone_id provided but invalid/inactive/wrong tenant → HTTP 404
      - zone_id None → fallback to location.delivery_fee (may be 0)

    fee is in tiyins (same unit as Order.total_amount).
    Client-provided fee is never used.
    """
    if order_type != "delivery":
        return (0, None)

    if zone_id is not None:
        zone = db.query(DeliveryZone).filter(
            DeliveryZone.id == zone_id,
            DeliveryZone.location_id == location.id,
            DeliveryZone.is_active,
        ).first()
        if not zone:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Delivery zone not found or inactive.",
            )
        return (zone.fee, zone.id)

    # Fallback: location-level fee (backward compat, also covers "no zones" scenario)
    return (location.delivery_fee or 0, None)


def resolve_effective_min_order(
    db: Session,
    location: Location,
    zone_id: Optional[int],
) -> int:
    """
    Returns effective minimum order amount (tiyins) for delivery.
    Zone min_order takes priority over location min_order_amount when > 0.
    """
    if zone_id is not None:
        zone = db.query(DeliveryZone).filter(
            DeliveryZone.id == zone_id,
            DeliveryZone.is_active,
        ).first()
        if zone and zone.min_order > 0:
            return zone.min_order
    return location.min_order_amount or 0


def validate_scheduled_at(scheduled_at: Optional[datetime]) -> None:
    """
    Validates scheduled_at against approved bounds-only rules (OD-01).
    Raises HTTP 422 if out of bounds.
    Does NOT check working hours (OD-01: structured hours deferred to future Phase).
    """
    if scheduled_at is None:
        return
    if scheduled_at.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scheduled_at must be timezone-aware (include UTC offset).",
        )
    now_utc = datetime.now(timezone.utc)
    min_time = now_utc + timedelta(minutes=_SCHEDULED_MIN_ADVANCE_MINUTES)
    max_time = now_utc + timedelta(days=_SCHEDULED_MAX_HORIZON_DAYS)
    if scheduled_at < min_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Scheduled time must be at least {_SCHEDULED_MIN_ADVANCE_MINUTES} "
                f"minutes from now."
            ),
        )
    if scheduled_at > max_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Scheduled time cannot be more than {_SCHEDULED_MAX_HORIZON_DAYS} "
                f"days in advance."
            ),
        )


# ── INTERNAL: CART ROW LOCK ────────────────────────────────────────

def _lock_active_cart(db: Session, cart: Cart) -> Cart:
    """
    Acquire exclusive row lock on active Cart (regular FOR UPDATE).
    Used by all Cart mutations to serialize against concurrent checkout.
    Returns refreshed Cart or raises HTTP 409 if cart no longer active.
    """
    locked = (
        db.query(Cart)
        .filter(Cart.id == cart.id, Cart.status == "active")
        .with_for_update()
        .first()
    )
    if locked is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cart is no longer active (already checked out or abandoned).",
        )
    return locked


# ── MODIFIER HASH ──────────────────────────────────────────────────

def compute_modifiers_hash(validated_option_ids: List[int]) -> str:
    if not validated_option_ids:
        return _EMPTY_MODIFIERS_HASH
    canonical = ",".join(str(i) for i in sorted(validated_option_ids))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── MODIFIER VALIDATION ────────────────────────────────────────────

def validate_modifiers(
    db: Session,
    product: Product,
    modifier_option_ids: List[int],
) -> List[dict]:
    if len(modifier_option_ids) != len(set(modifier_option_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate modifier_option_ids are not allowed.",
        )

    option_map: dict = {}
    active_groups: dict = {}

    for group in product.modifier_groups:
        if not group.is_active:
            continue
        active_groups[group.id] = group
        for opt in group.options:
            if opt.is_active:
                option_map[opt.id] = (group, opt)

    if not modifier_option_ids:
        for group in active_groups.values():
            if group.min_selections > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Modifier group '{group.name}' requires at least "
                        f"{group.min_selections} selection(s)."
                    ),
                )
        return []

    for opt_id in modifier_option_ids:
        if opt_id not in option_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Modifier option id={opt_id} is not available for this product.",
            )
        _group, opt = option_map[opt_id]
        if not opt.is_available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Modifier option '{opt.name}' is currently unavailable.",
            )

    group_counts: dict = {}
    for opt_id in modifier_option_ids:
        group, _ = option_map[opt_id]
        group_counts[group.id] = group_counts.get(group.id, 0) + 1

    for group in active_groups.values():
        count = group_counts.get(group.id, 0)
        if count < group.min_selections:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Modifier group '{group.name}' requires at least "
                    f"{group.min_selections} selection(s), got {count}."
                ),
            )
        if count > group.max_selections:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Modifier group '{group.name}' allows at most "
                    f"{group.max_selections} selection(s), got {count}."
                ),
            )

    return [
        {
            "modifier_option_id": option_map[opt_id][1].id,
            "name":               option_map[opt_id][1].name,
            "price_adjustment":   option_map[opt_id][1].price_adjustment,
        }
        for opt_id in modifier_option_ids
    ]


# ── CART RESPONSE BUILDER ──────────────────────────────────────────

def build_cart_response(cart: Cart, db: Session) -> CartResponse:
    cart_obj = (
        db.query(Cart)
        .filter(Cart.id == cart.id)
        .options(
            joinedload(Cart.items).joinedload(CartItem.product),
            joinedload(Cart.items).joinedload(CartItem.variant),
            joinedload(Cart.items).joinedload(CartItem.modifiers),
        )
        .first()
    )
    if not cart_obj:
        return CartResponse(
            cart_id=cart.id,
            restaurant_id=cart.restaurant_id,
            currency=cart.currency,
            status=cart.status,
            items=[],
            subtotal=0,
            item_count=0,
        )

    item_responses = []
    for item in cart_obj.items:
        item_responses.append(CartItemResponse(
            id=item.id,
            product_id=item.product_id,
            product_name=item.product.name if item.product else "",
            variant_id=item.variant_id,
            variant_name=item.variant.name if item.variant else None,
            quantity=item.quantity,
            unit_price=item.unit_price,
            modifiers=[
                CartItemModifierResponse(
                    modifier_option_id=m.modifier_option_id,
                    name=m.name,
                    price_adjustment=m.price_adjustment,
                )
                for m in item.modifiers
            ],
            notes=item.notes,
            line_total=item.line_total,
        ))

    return CartResponse(
        cart_id=cart_obj.id,
        restaurant_id=cart_obj.restaurant_id,
        currency=cart_obj.currency,
        status=cart_obj.status,
        items=item_responses,
        subtotal=sum(i.line_total for i in cart_obj.items),
        item_count=sum(i.quantity for i in cart_obj.items),
    )


# ── GET OR CREATE CART ─────────────────────────────────────────────

def get_or_create_cart(
    db: Session,
    restaurant_id: int,
    session_id: str,
    telegram_id: Optional[int],
    currency: str,
) -> Cart:
    cart = (
        db.query(Cart)
        .filter(
            Cart.session_id == session_id,
            Cart.restaurant_id == restaurant_id,
            Cart.status == "active",
        )
        .first()
    )
    if cart is not None:
        if cart.currency != currency:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cart currency ({cart.currency}) does not match "
                    f"location currency ({currency})."
                ),
            )
        return cart

    cart = Cart(
        restaurant_id=restaurant_id,
        session_id=session_id,
        telegram_id=telegram_id,
        currency=currency,
        status="active",
    )
    db.add(cart)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        cart = (
            db.query(Cart)
            .filter(
                Cart.session_id == session_id,
                Cart.restaurant_id == restaurant_id,
                Cart.status == "active",
            )
            .first()
        )
        if cart is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create or retrieve cart after concurrent conflict.",
            )
        if cart.currency != currency:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cart currency ({cart.currency}) does not match "
                    f"location currency ({currency})."
                ),
            )
    return cart


# ── GET CART ───────────────────────────────────────────────────────

def get_cart(
    db: Session,
    restaurant_id: int,
    session_id: str,
) -> Optional[Cart]:
    return (
        db.query(Cart)
        .filter(
            Cart.session_id == session_id,
            Cart.restaurant_id == restaurant_id,
            Cart.status == "active",
        )
        .first()
    )


# ── ADD ITEM ───────────────────────────────────────────────────────

def add_item(
    db: Session,
    cart: Cart,
    product_id: int,
    variant_id: Optional[int],
    modifier_option_ids: List[int],
    notes: Optional[str],
    quantity: int,
    location: Location,
) -> Cart:
    """
    Phase 7: Acquires SELECT Cart FOR UPDATE before any mutation.
    Serializes add_item against concurrent checkout_cart.
    """
    # Phase 7: Cart row lock
    cart = _lock_active_cart(db, cart)

    product = (
        db.query(Product)
        .filter(
            Product.id == product_id,
            Product.restaurant_id == cart.restaurant_id,
        )
        .options(joinedload(Product.modifier_groups).joinedload(ModifierGroup.options))
        .first()
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product id={product_id} not found in this restaurant.",
        )
    if not product.is_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product '{product.name}' is not available.",
        )
    tz_str = location.timezone or "Asia/Tashkent"
    if not is_within_schedule(product.available_from, product.available_until, tz_str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product '{product.name}' is not available at this time.",
        )

    variant: Optional[ProductVariant] = None
    if variant_id is not None:
        variant = (
            db.query(ProductVariant)
            .filter(
                ProductVariant.id == variant_id,
                ProductVariant.product_id == product.id,
            )
            .first()
        )
        if not variant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Variant id={variant_id} not found for product id={product_id}.",
            )
        if not variant.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Variant '{variant.name}' is inactive.",
            )
        if not variant.is_available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Variant '{variant.name}' is currently unavailable.",
            )
        base_price = variant.price
    else:
        if product.price is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product '{product.name}' requires a variant_id.",
            )
        base_price = product.price

    modifier_snapshots = validate_modifiers(db, product, modifier_option_ids)
    modifier_total = sum(m["price_adjustment"] for m in modifier_snapshots)
    unit_price = max(0, base_price + modifier_total)
    line_total = unit_price * quantity
    modifiers_hash = compute_modifiers_hash(modifier_option_ids)
    variant_id_val = variant.id if variant else None

    result = db.execute(
        sa_text("""
            INSERT INTO cart_items
                (cart_id, product_id, variant_id, quantity, unit_price,
                 modifiers_hash, notes, line_total)
            VALUES
                (:cart_id, :product_id, :variant_id, :quantity, :unit_price,
                 :modifiers_hash, :notes, :line_total)
            ON CONFLICT (cart_id, product_id, COALESCE(variant_id, 0), modifiers_hash)
            DO UPDATE SET
                quantity   = cart_items.quantity + EXCLUDED.quantity,
                line_total = (cart_items.quantity + EXCLUDED.quantity)
                             * cart_items.unit_price,
                notes      = EXCLUDED.notes,
                updated_at = now()
            RETURNING id
        """),
        {
            "cart_id": cart.id, "product_id": product.id,
            "variant_id": variant_id_val, "quantity": quantity,
            "unit_price": unit_price, "modifiers_hash": modifiers_hash,
            "notes": notes, "line_total": line_total,
        },
    )
    cart_item_id = result.scalar()

    if modifier_snapshots:
        existing_count = (
            db.query(CartItemModifier)
            .filter(CartItemModifier.cart_item_id == cart_item_id)
            .count()
        )
        if existing_count == 0:
            for snap in modifier_snapshots:
                db.add(CartItemModifier(
                    cart_item_id=cart_item_id,
                    modifier_option_id=snap["modifier_option_id"],
                    name=snap["name"],
                    price_adjustment=snap["price_adjustment"],
                ))

    db.commit()
    db.refresh(cart)
    return cart


# ── UPDATE QUANTITY ────────────────────────────────────────────────

def update_item_quantity(
    db: Session,
    cart: Cart,
    item_id: int,
    new_quantity: int,
) -> Cart:
    """Phase 7: Cart row lock (replaces per-CartItem FOR UPDATE)."""
    cart = _lock_active_cart(db, cart)

    cart_item = (
        db.query(CartItem)
        .filter(CartItem.id == item_id, CartItem.cart_id == cart.id)
        .first()
    )
    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cart item id={item_id} not found in this cart.",
        )
    cart_item.quantity = new_quantity
    cart_item.line_total = cart_item.unit_price * new_quantity
    db.commit()
    db.refresh(cart)
    return cart


# ── REMOVE ITEM ────────────────────────────────────────────────────

def remove_item(db: Session, cart: Cart, item_id: int) -> Cart:
    """Phase 7: Cart row lock before DELETE."""
    cart = _lock_active_cart(db, cart)

    cart_item = (
        db.query(CartItem)
        .filter(CartItem.id == item_id, CartItem.cart_id == cart.id)
        .first()
    )
    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cart item id={item_id} not found in this cart.",
        )
    db.delete(cart_item)
    db.commit()
    db.refresh(cart)
    return cart


# ── CLEAR CART ─────────────────────────────────────────────────────

def clear_cart(db: Session, cart: Cart) -> None:
    """Phase 7: Cart row lock before DELETE ALL."""
    cart = _lock_active_cart(db, cart)
    db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
    db.commit()


# ── CHECKOUT CART (Phase 7) ────────────────────────────────────────

def checkout_cart(
    db: Session,
    session_id: str,
    restaurant_id: int,
    location: Location,
    telegram_id: Optional[int],
    order_type: str,
    client_name: Optional[str],
    client_phone: Optional[str],
    address: Optional[str],
    table_id: Optional[int],
    comment: Optional[str],
    idempotency_key: Optional[str],
    display_name: str,
    # Phase 14 additions:
    zone_id: Optional[int] = None,
    scheduled_at: Optional[datetime] = None,
    location_lat: Optional[float] = None,
    location_lng: Optional[float] = None,
) -> Order:
    """
    Convert active Cart into immutable Order.

    Transaction:
      SELECT cart FOR UPDATE NOWAIT
      → idempotency check (replay if same key)
      → validate not empty
      → fresh availability validation
      → calculate subtotal (CartItem.unit_price snapshots)
      → resolve delivery_fee (Phase 14: server-side, zone or location fallback)
      → total_amount = subtotal + delivery_fee
      → validate min_order against subtotal
      → validate scheduled_at bounds (Phase 14)
      → INSERT Order + OrderItems + OrderItemModifiers
      → UPDATE cart: status='checked_out', checkout_idempotency_key=key, order_id=order.id
      → COMMIT

    Failure: ROLLBACK → Cart remains active, no Order created.
    """
    # Step 1: Lock Cart row NOWAIT
    try:
        cart = (
            db.query(Cart)
            .filter(
                Cart.session_id == session_id,
                Cart.restaurant_id == restaurant_id,
            )
            .with_for_update(nowait=True)
            .first()
        )
    except OperationalError as exc:
        pg_code = getattr(getattr(exc, "orig", None), "pgcode", None)
        if pg_code == _PG_LOCK_NOT_AVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Checkout is already in progress for this cart. Please retry.",
            )
        raise

    # Step 2: Cart not found
    if cart is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart not found for this session and restaurant.",
        )

    # Step 3: Already checked out — idempotency replay
    if cart.status == "checked_out":
        if (
            idempotency_key is not None
            and cart.checkout_idempotency_key == idempotency_key
        ):
            existing_order = _find_order_for_checked_out_cart(db, cart, restaurant_id)
            if existing_order:
                # Phase 13: On idempotency replay, we cannot recover the raw token
                # (only SHA-256 hash is stored). Mark as replay so router knows
                # NOT to include web_order_token in response (client has it from
                # the original response, stored in localStorage).
                existing_order._web_order_token_raw = None  # type: ignore[attr-defined]
                existing_order._is_idempotency_replay = True  # type: ignore[attr-defined]
                return existing_order
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cart is checked_out but no Order was found. Contact support.",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cart has already been checked out.",
        )

    # Step 4: Must be active
    if cart.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cart has status '{cart.status}' and cannot be checked out.",
        )

    # Step 5: Load CartItems
    items = (
        db.query(CartItem)
        .filter(CartItem.cart_id == cart.id)
        .options(
            joinedload(CartItem.modifiers),
            joinedload(CartItem.product).joinedload(
                Product.modifier_groups
            ).joinedload(ModifierGroup.options),
            joinedload(CartItem.variant),
        )
        .all()
    )

    if not items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot checkout an empty cart.",
        )

    # Step 6: Fresh availability validation
    tz_str = location.timezone or "Asia/Tashkent"
    for item in items:
        product = item.product
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A product in your cart no longer exists.",
            )
        if getattr(product, "is_active", True) is False:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Product '{product.name}' is no longer active.",
            )
        if not product.is_available:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Product '{product.name}' is currently unavailable.",
            )
        if not is_within_schedule(product.available_from, product.available_until, tz_str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Product '{product.name}' is not available at this time.",
            )
        if item.variant_id is not None:
            variant = item.variant
            if variant is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"A variant for product '{product.name}' no longer exists.",
                )
            if not variant.is_active:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Variant '{variant.name}' is no longer active.",
                )
            if not variant.is_available:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Variant '{variant.name}' is currently unavailable.",
                )
        if item.modifiers:
            current_opt_avail: dict = {}
            for group in product.modifier_groups:
                for opt in group.options:
                    current_opt_avail[opt.id] = (
                        group.is_active and opt.is_active and opt.is_available
                    )
            for cart_mod in item.modifiers:
                if cart_mod.modifier_option_id is None:
                    continue
                if current_opt_avail.get(cart_mod.modifier_option_id) is False:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Modifier option '{cart_mod.name}' is currently unavailable.",
                    )

    # Step 7: Server-authoritative subtotal (ADR-P7-PRICE + ADR-P7-1).
    # subtotal = items only; delivery_fee added separately below.
    # Client cannot supply subtotal, fee, or total_amount.
    subtotal = sum(item.unit_price * item.quantity for item in items)

    # Step 7a: Phase 14 — delivery fee (server-side, zone or location fallback).
    # resolve_delivery_fee raises HTTP 404 for invalid/inactive/cross-tenant zone_id.
    delivery_fee, resolved_zone_id = resolve_delivery_fee(db, location, order_type, zone_id)
    total_amount = subtotal + delivery_fee

    # Step 7b: minimum order check against subtotal (items only, not total_amount).
    # Phase 14: zone min_order takes priority over location min_order_amount.
    _min_order = resolve_effective_min_order(db, location, resolved_zone_id)
    _currency = cart.currency or "UZS"
    if order_type == "delivery" and _min_order > 0 and subtotal < _min_order:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Минимальная сумма заказа для доставки: "
                f"{_fmt_price(_min_order, _currency)}. "
                f"Ваш заказ: {_fmt_price(subtotal, _currency)}."
            ),
        )

    # Step 7c: Phase 14 — scheduled order validation (OD-01: bounds-only).
    validate_scheduled_at(scheduled_at)

    # Step 8: order_type business rules
    if order_type == "delivery" and not address:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Address is required for delivery orders.",
        )
    if order_type == "dine_in" and not table_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Table ID is required for dine_in orders.",
        )

    # Phase 11: non-dine_in must NOT supply table_id
    if order_type != "dine_in" and table_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="table_id is only allowed for dine_in orders.",
        )

    # Phase 11: dine_in table ownership validation (object-level authorization)
    # Client-supplied table_id must belong to both the current restaurant AND location.
    # Unified 404 on any mismatch — no information leakage about ownership structure.
    if order_type == "dine_in" and table_id is not None:
        table_obj = db.query(RestaurantTable).filter(
            RestaurantTable.id == table_id,
            RestaurantTable.restaurant_id == restaurant_id,
            RestaurantTable.location_id == location.id,
        ).first()
        if not table_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Table not found.",
            )

    # Step 9: Create Order
    # Phase 13: generate web_order_token for anonymous (non-Telegram) web orders.
    # Raw token is cryptographically random (256-bit entropy via secrets module).
    # ONLY the SHA-256 hash is stored in DB — raw token is NEVER persisted.
    # Token is set on the Order object AFTER flush so it can be read by the caller.
    _is_web_guest = telegram_id is None or telegram_id == 0
    _raw_web_token: Optional[str] = None
    _token_hash: Optional[str] = None
    if _is_web_guest:
        _raw_web_token = secrets.token_urlsafe(32)  # ~43 chars, 256-bit entropy
        _token_hash = hashlib.sha256(_raw_web_token.encode()).hexdigest()  # 64-char hex

    # Phase 14: scheduled orders start as 'new' (not visible in KDS until activation).
    # Immediate orders start as 'accepted' (existing behaviour unchanged).
    _initial_status = "new" if scheduled_at is not None else "accepted"

    order = Order(
        restaurant_id=restaurant_id,
        location_id=location.id,
        client_telegram_id=telegram_id,
        client_name=client_name or display_name,
        client_phone=client_phone,
        order_type=order_type,
        address=address,
        table_id=table_id,
        comment=comment,
        subtotal=subtotal,                  # Phase 14: items-only amount
        delivery_fee=delivery_fee,          # Phase 14: fee snapshot (0 for non-delivery)
        total_amount=total_amount,          # Phase 14: subtotal + delivery_fee
        delivery_zone_id=resolved_zone_id,  # Phase 14: FK to zone (nullable)
        scheduled_at=scheduled_at,          # Phase 14: None for immediate orders
        currency=cart.currency,             # immutable snapshot
        status=_initial_status,
        web_order_token_hash=_token_hash,   # None for Telegram orders
        location_lat=location_lat,          # Phase 14: optional coordinates
        location_lng=location_lng,          # Phase 14: optional coordinates
    )
    db.add(order)
    db.flush()

    # Step 10: Immutable snapshots
    for item in items:
        product = item.product
        variant = item.variant if item.variant_id is not None else None

        order_item = OrderItem(
            order_id=order.id,
            product_id=item.product_id,
            name=product.name,
            variant_id=item.variant_id,
            variant_name=variant.name if variant else None,
            price=item.unit_price,
            quantity=item.quantity,
        )
        db.add(order_item)
        db.flush()

        for cart_mod in item.modifiers:
            db.add(OrderItemModifier(
                order_item_id=order_item.id,
                modifier_option_id=cart_mod.modifier_option_id,
                name=cart_mod.name,
                price_adjustment=cart_mod.price_adjustment,
            ))

    # Step 11: Mark Cart checked_out + store idempotency key + order_id link (atomic).
    # cart.order_id set here (same transaction as status='checked_out') — Phase 14 SEC-03.
    cart.status = "checked_out"
    cart.checkout_idempotency_key = idempotency_key
    cart.order_id = order.id  # Phase 14: direct FK for safe idempotency replay

    # Step 12: Commit
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        err_str = str(exc).lower()
        if "checkout_idempotency_key" in err_str or "uq_carts_checkout_idempotency" in err_str:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This idempotency key has already been used for another cart.",
            )
        raise

    db.refresh(order)
    # Phase 13: attach raw token to Order object (transient, not a DB column).
    # Router reads order._web_order_token_raw and includes it in checkout response.
    # Raw token is never stored — only set here transiently in memory.
    order._web_order_token_raw = _raw_web_token  # type: ignore[attr-defined]
    return order


# ── INTERNAL: FIND ORDER FOR CHECKED-OUT CART ──────────────────────

def _find_order_for_checked_out_cart(
    db: Session,
    cart: Cart,
    restaurant_id: int,
) -> Optional[Order]:
    """
    Safe idempotency replay via direct cart.order_id FK (Phase 14, SEC-03).
    Falls back to heuristic for pre-Phase-14 carts (order_id is NULL).
    """
    # Phase 14: O(1) direct lookup — guaranteed correct, no cross-cart risk.
    if cart.order_id is not None:
        return db.query(Order).filter(Order.id == cart.order_id).first()

    # Legacy fallback for pre-Phase-14 carts (order_id=NULL).
    # Phase 15+: remove once all active carts have order_id.
    logger.debug(
        "_find_order_for_checked_out_cart: cart %s has no order_id, using legacy lookup",
        cart.id,
    )
    return (
        db.query(Order)
        .filter(
            Order.restaurant_id == restaurant_id,
            Order.currency == cart.currency,
        )
        .order_by(Order.created_at.desc())
        .limit(1)
        .first()
    )
