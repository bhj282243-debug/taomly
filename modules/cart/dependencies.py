"""
modules/cart/dependencies.py — Taomly Platform
Phase 6: Cart Engine FastAPI dependencies.

CartContext: resolved per-request context combining:
  - tg_user (from get_telegram_user — authoritative restaurant identity)
  - location (validated: location.restaurant_id == tg_user.restaurant_id)
  - session_id (from X-Cart-Session header — opaque UUID4 bearer token)

Security:
  - restaurant_id ALWAYS from tg_user.restaurant_id (DB-loaded object)
  - X-Restaurant-Id is a routing key for get_telegram_user() only
  - X-Location-Id validated against authoritative restaurant
  - session_id treated as opaque bearer — not interpreted or trusted beyond ownership
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from auth import TelegramUser, get_telegram_user
from database import get_db
from models import Location


@dataclass
class CartContext:
    """
    Resolved per-request context for Cart operations.
    All fields are authoritative — sourced from DB, not from client input.
    """
    tg_user:    TelegramUser
    location:   Location
    session_id: str

    @property
    def restaurant_id(self) -> int:
        return self.tg_user.restaurant_id

    @property
    def telegram_id(self) -> Optional[int]:
        uid = self.tg_user.id
        return uid if uid > 0 else None


def get_cart_context(
    x_cart_session: str          = Header(..., alias="X-Cart-Session"),
    x_location_id:  int          = Header(..., alias="X-Location-Id"),
    tg_user:        TelegramUser = Depends(get_telegram_user),
    db:             Session      = Depends(get_db),
) -> CartContext:
    """
    Resolves and validates the full Cart request context.

    X-Restaurant-Id → get_telegram_user() → tg_user.restaurant_id (authoritative)
    X-Location-Id   → validated against tg_user.restaurant_id
    X-Cart-Session  → opaque session_id (not validated beyond presence)
    """
    if not x_cart_session or not x_cart_session.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="X-Cart-Session header is required and must not be empty.",
        )

    location = (
        db.query(Location)
        .filter(
            Location.id == x_location_id,
            Location.restaurant_id == tg_user.restaurant_id,  # tenant check
            Location.is_active == True,
        )
        .first()
    )
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found or does not belong to this restaurant.",
        )

    return CartContext(
        tg_user=tg_user,
        location=location,
        session_id=x_cart_session.strip(),
    )


def get_cart_context_read(
    x_cart_session: str          = Header(..., alias="X-Cart-Session"),
    x_location_id:  Optional[int] = Header(None, alias="X-Location-Id"),
    tg_user:        TelegramUser = Depends(get_telegram_user),
    db:             Session      = Depends(get_db),
) -> CartContext:
    """
    Read-only variant of get_cart_context.
    X-Location-Id is optional for GET /api/cart — no schedule check needed.
    Falls back to first active location of the restaurant for currency context.
    """
    if not x_cart_session or not x_cart_session.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="X-Cart-Session header is required and must not be empty.",
        )

    if x_location_id is not None:
        location = (
            db.query(Location)
            .filter(
                Location.id == x_location_id,
                Location.restaurant_id == tg_user.restaurant_id,
                Location.is_active == True,
            )
            .first()
        )
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Location not found or does not belong to this restaurant.",
            )
    else:
        location = (
            db.query(Location)
            .filter(
                Location.restaurant_id == tg_user.restaurant_id,
                Location.is_active == True,
            )
            .order_by(Location.id)
            .first()
        )
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active location found for this restaurant.",
            )

    return CartContext(
        tg_user=tg_user,
        location=location,
        session_id=x_cart_session.strip(),
    )
