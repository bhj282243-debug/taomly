"""
modules/cart/service.py — Taomly Platform
Phase 6: Cart Engine business logic.

All pricing is server-authoritative. Client-supplied prices are never used.
Modifier duplicate IDs → HTTP 400 (not silently deduplicated).
CartItem identity enforced via INSERT ... ON CONFLICT DO UPDATE.
unit_price is a snapshot — never updated on subsequent adds.
"""

import hashlib
import logging
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, joinedload

from models import Location, ModifierGroup, ModifierOption, Product, ProductVariant
from modules.cart.models import Cart, CartItem, CartItemModifier
from modules.cart.schemas import CartItemModifierResponse, CartItemResponse, CartResponse
from utils import is_within_schedule

logger = logging.getLogger(__name__)

# SHA-256 of empty string — canonical modifiers_hash for items with no modifiers.
_EMPTY_MODIFIERS_HASH = hashlib.sha256(b"").hexdigest()  # 64 chars


# ──────────────────────────────────────────
# MODIFIER HASH
# ──────────────────────────────────────────

def compute_modifiers_hash(validated_option_ids: List[int]) -> str:
    """
    Deterministic canonical hash for a validated set of modifier option IDs.

    Empty list → sha256("") constant (not empty string in DB).
    Non-empty  → sha256(sorted comma-joined ids) full 64-char hex.
    """
    if not validated_option_ids:
        return _EMPTY_MODIFIERS_HASH
    canonical = ",".join(str(i) for i in sorted(validated_option_ids))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ──────────────────────────────────────────
# MODIFIER VALIDATION
# ──────────────────────────────────────────

def validate_modifiers(
    db: Session,
    product: Product,
    modifier_option_ids: List[int],
) -> List[dict]:
    """
    Validate modifier_option_ids for a given product and return snapshot list.

    Raises HTTP 400 on:
      - duplicate option IDs (never silently deduplicated)
      - unknown / wrong-product / inactive option or group
      - min_selections not met (required group)
      - max_selections exceeded

    Returns list of snapshot dicts:
      [{"modifier_option_id": int, "name": str, "price_adjustment": int}, ...]
    """
    # Step 1: duplicate detection — reject, never deduplicate
    if len(modifier_option_ids) != len(set(modifier_option_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate modifier_option_ids are not allowed.",
        )

    # Build lookup: active options in active groups for this product
    option_map: dict = {}  # option_id → (group, option)
    active_groups: dict = {}  # group_id → group

    for group in product.modifier_groups:
        if not group.is_active:
            continue
        active_groups[group.id] = group
        for opt in group.options:
            if opt.is_active:
                option_map[opt.id] = (group, opt)

    # Empty modifier_option_ids — check required groups
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

    # Step 2: validate each requested option
    for opt_id in modifier_option_ids:
        if opt_id not in option_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Modifier option id={opt_id} is not available for this product. "
                    "It may be inactive, belong to another product, or not exist."
                ),
            )
        _group, opt = option_map[opt_id]
        if not opt.is_available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Modifier option '{opt.name}' is currently unavailable.",
            )

    # Step 3: min/max per group
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

    # Step 4: build snapshot list (DB values only)
    return [
        {
            "modifier_option_id": option_map[opt_id][1].id,
            "name": option_map[opt_id][1].name,
            "price_adjustment": option_map[opt_id][1].price_adjustment,
        }
        for opt_id in modifier_option_ids
    ]


# ──────────────────────────────────────────
# CART RESPONSE BUILDER
# ──────────────────────────────────────────

def build_cart_response(cart: Cart, db: Session) -> CartResponse:
    """Build CartResponse from Cart id, loading relationships eagerly."""
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


# ──────────────────────────────────────────
# GET OR CREATE CART
# ──────────────────────────────────────────

def get_or_create_cart(
    db: Session,
    restaurant_id: int,
    session_id: str,
    telegram_id: Optional[int],
    currency: str,
) -> Cart:
    """
    Get existing active cart or create a new one (lazy creation).
    Currency mismatch → HTTP 409.
    """
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
                    f"location currency ({currency}). "
                    "Clear the cart or use a location with matching currency."
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
    db.flush()
    return cart


# ──────────────────────────────────────────
# GET CART (read-only)
# ──────────────────────────────────────────

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


# ──────────────────────────────────────────
# ADD ITEM
# ──────────────────────────────────────────

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
    Add item to cart with full server-side validation and pricing.

    Pricing: unit_price = base_price + sum(modifier.price_adjustment)
    Concurrency: INSERT ... ON CONFLICT DO UPDATE (atomic, no race window).
    Snapshot: unit_price NOT updated on conflict — existing snapshot preserved.
    """
    # 1. Load product with modifier groups + options eagerly
    product = (
        db.query(Product)
        .filter(
            Product.id == product_id,
            Product.restaurant_id == cart.restaurant_id,
        )
        .options(
            joinedload(Product.modifier_groups).joinedload(ModifierGroup.options)
        )
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

    # 2. Variant or legacy price
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
                detail=(
                    f"Product '{product.name}' requires a variant_id "
                    "(it has variants, no direct price)."
                ),
            )
        base_price = product.price

    # 3. Validate modifiers
    modifier_snapshots = validate_modifiers(db, product, modifier_option_ids)
    modifier_total = sum(m["price_adjustment"] for m in modifier_snapshots)

    # 4. Server-authoritative pricing
    unit_price = max(0, base_price + modifier_total)
    line_total = unit_price * quantity
    modifiers_hash = compute_modifiers_hash(modifier_option_ids)
    variant_id_val = variant.id if variant else None

    # 5. Atomic INSERT ... ON CONFLICT DO UPDATE
    #    Mirrors the functional unique index: (cart_id, product_id, COALESCE(variant_id,0), modifiers_hash)
    #    On conflict: increment quantity, recalculate line_total using EXISTING unit_price.
    #    unit_price NOT in SET → snapshot semantics preserved.
    insert_stmt = (
        pg_insert(CartItem.__table__)
        .values(
            cart_id=cart.id,
            product_id=product.id,
            variant_id=variant_id_val,
            quantity=quantity,
            unit_price=unit_price,
            modifiers_hash=modifiers_hash,
            notes=notes,
            line_total=line_total,
        )
        .on_conflict_do_update(
            constraint="uq_cart_item_identity",
            set_={
                "quantity":   CartItem.__table__.c.quantity + quantity,
                "line_total": (CartItem.__table__.c.quantity + quantity)
                              * CartItem.__table__.c.unit_price,
                "notes":      notes,
            },
        )
        .returning(CartItem.__table__.c.id)
    )
    result = db.execute(insert_stmt)
    cart_item_id = result.scalar()

    # 6. Insert modifier snapshots only if this was a fresh insert
    #    (no modifiers exist for the item yet)
    if modifier_snapshots:
        existing = (
            db.query(CartItemModifier)
            .filter(CartItemModifier.cart_item_id == cart_item_id)
            .count()
        )
        if existing == 0:
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


# ──────────────────────────────────────────
# UPDATE QUANTITY
# ──────────────────────────────────────────

def update_item_quantity(
    db: Session,
    cart: Cart,
    item_id: int,
    new_quantity: int,
) -> Cart:
    """
    Update CartItem quantity. SELECT FOR UPDATE for safe concurrent updates.
    unit_price never changed. line_total = unit_price × new_quantity.
    """
    cart_item = (
        db.query(CartItem)
        .filter(CartItem.id == item_id, CartItem.cart_id == cart.id)
        .with_for_update()
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


# ──────────────────────────────────────────
# REMOVE ITEM
# ──────────────────────────────────────────

def remove_item(
    db: Session,
    cart: Cart,
    item_id: int,
) -> Cart:
    """Delete a CartItem (cascade deletes its modifiers)."""
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


# ──────────────────────────────────────────
# CLEAR CART
# ──────────────────────────────────────────

def clear_cart(db: Session, cart: Cart) -> None:
    """Delete all items. Cart record remains active."""
    db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
    db.commit()
