"""
routers/delivery_zones.py — Taomly Platform
Phase 14: Delivery Zone management and public listing.

Admin endpoints (require restaurant admin JWT):
  POST   /api/restaurants/{restaurant_id}/delivery-zones
  GET    /api/restaurants/{restaurant_id}/delivery-zones
  PATCH  /api/delivery-zones/{zone_id}
  DELETE /api/delivery-zones/{zone_id}   (soft delete: is_active=False)

Public endpoint (no auth):
  GET    /api/locations/{location_slug}/delivery-zones

Tenant isolation:
  All zone mutations validate zone.location.restaurant_id == admin.id.
  Zone creation validates location_id → location.restaurant_id == admin.id.

Business rules:
  - fee/min_order in tiyins (same unit as Order.total_amount).
  - Soft delete only (is_active=False). Hard delete requires no active zones.
  - Public listing returns only is_active=True zones ordered by sort_order ASC.
"""

# ruff: noqa: I001
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from auth import get_current_restaurant_admin
from database import get_db
from limiter import limiter
from models import DeliveryZone, Location, Restaurant
from schemas.delivery_zones import (
    DeliveryZoneCreate,
    DeliveryZonePublicResponse,
    DeliveryZoneResponse,
    DeliveryZoneUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["delivery-zones"])


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _get_zone_for_admin(
    zone_id: int,
    admin: Restaurant,
    db: Session,
) -> DeliveryZone:
    """Load zone and verify it belongs to the authenticated admin's restaurant."""
    zone = (
        db.query(DeliveryZone)
        .join(Location, Location.id == DeliveryZone.location_id)
        .filter(
            DeliveryZone.id == zone_id,
            Location.restaurant_id == admin.id,
        )
        .first()
    )
    if not zone:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery zone not found.")
    return zone


# ── ADMIN: CREATE ──────────────────────────────────────────────────────────────

@router.post(
    "/api/restaurants/{restaurant_id}/delivery-zones",
    response_model=DeliveryZoneResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_delivery_zone(
    restaurant_id: int,
    body: DeliveryZoneCreate,
    location_id: int,                               # required query param
    admin: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Create a delivery zone for a location. location_id must belong to admin's restaurant."""
    if admin.id != restaurant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")

    location = db.query(Location).filter(
        Location.id == location_id,
        Location.restaurant_id == admin.id,
        Location.is_active,
    ).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found or does not belong to this restaurant.",
        )

    zone = DeliveryZone(
        location_id=location_id,
        name=body.name,
        fee=body.fee,
        min_order=body.min_order,
        eta_minutes=body.eta_minutes,
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    logger.info(
        "DeliveryZone created: id=%s location_id=%s name=%r restaurant_id=%s",
        zone.id, zone.location_id, zone.name, admin.id,
    )
    return zone


# ── ADMIN: LIST ────────────────────────────────────────────────────────────────

@router.get(
    "/api/restaurants/{restaurant_id}/delivery-zones",
    response_model=list[DeliveryZoneResponse],
)
def list_delivery_zones_admin(
    restaurant_id: int,
    location_id: int | None = None,
    include_inactive: bool = False,
    admin: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """List delivery zones for admin. Optionally filter by location_id."""
    if admin.id != restaurant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")

    q = (
        db.query(DeliveryZone)
        .join(Location, Location.id == DeliveryZone.location_id)
        .filter(Location.restaurant_id == admin.id)
    )
    if location_id is not None:
        q = q.filter(DeliveryZone.location_id == location_id)
    if not include_inactive:
        q = q.filter(DeliveryZone.is_active == True)
    return q.order_by(DeliveryZone.sort_order.asc(), DeliveryZone.id.asc()).all()


# ── ADMIN: UPDATE ──────────────────────────────────────────────────────────────

@router.patch(
    "/api/delivery-zones/{zone_id}",
    response_model=DeliveryZoneResponse,
)
def update_delivery_zone(
    zone_id: int,
    body: DeliveryZoneUpdate,
    admin: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    zone = _get_zone_for_admin(zone_id, admin, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(zone, field, value)
    db.commit()
    db.refresh(zone)
    logger.info("DeliveryZone updated: id=%s restaurant_id=%s", zone.id, admin.id)
    return zone


# ── ADMIN: SOFT DELETE ─────────────────────────────────────────────────────────

@router.delete(
    "/api/delivery-zones/{zone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def deactivate_delivery_zone(
    zone_id: int,
    admin: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Soft-delete: sets is_active=False. Does not delete from DB."""
    zone = _get_zone_for_admin(zone_id, admin, db)
    zone.is_active = False
    db.commit()
    logger.info("DeliveryZone deactivated: id=%s restaurant_id=%s", zone.id, admin.id)


# ── PUBLIC: LIST FOR CUSTOMER ──────────────────────────────────────────────────

@router.get(
    "/api/locations/{location_slug}/delivery-zones",
    response_model=list[DeliveryZonePublicResponse],
)
@limiter.limit("30/minute")
def list_delivery_zones_public(
    request: Request,
    location_slug: str,
    db: Session = Depends(get_db),
):
    """
    Returns active delivery zones for a location (customer-facing).
    No auth required. Returns only is_active=True zones.
    """
    location = db.query(Location).filter(
        Location.slug == location_slug.lower().strip(),
        Location.is_active,
    ).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found.",
        )

    zones = (
        db.query(DeliveryZone)
        .filter(
            DeliveryZone.location_id == location.id,
            DeliveryZone.is_active,
        )
        .order_by(DeliveryZone.sort_order.asc(), DeliveryZone.id.asc())
        .all()
    )
    return zones
