"""
routers/public_web.py — Taomly Platform
Phase 13: Public Web Ordering

GET /r/{slug} — SSR restaurant page for web ordering.

Resolution:
  Step 1: Location.slug (canonical, OD-04)
  Step 2: Restaurant.slug fallback (backward compat)
  Case C: Restaurant exists but no active Location → 404 (Correction 2)

SEO: Jinja2 SSR renders title, description, canonical, OG meta server-side.
Branding: CSS variables inlined in <style> block for zero-flash initial paint.
Cart: JS hydration after page load via existing GET /api/restaurants/{slug}.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from limiter import limiter
from models import Location, Restaurant

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="templates")

# ─────────────────────────────────────────────────────────────────────────────
# SHARED SLUG RESOLVER (OD-04 + Correction 2)
#
# Called by GET /r/{slug} (public web) and also usable by future endpoints.
#
# Resolution order (matches existing GET /api/restaurants/{slug}):
#   Step 1: Location.slug == slug AND Location.is_active == True
#           → derive Restaurant (must be active too)
#   Step 2: Restaurant.slug == slug AND Restaurant.is_active == True
#           → first active Location ORDER BY Location.id
#
# Correction 2: if Restaurant exists but no active Location → raise 404.
# This is stricter than the existing API endpoint (which gracefully degrades).
# Web ordering requires an active Location to initialize the Cart.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_slug(slug: str, db: Session):
    """
    Resolve a public slug to (restaurant, location).
    Raises HTTP 404 in all failure cases (no info leakage).

    Returns:
        (Restaurant, Location) tuple — both guaranteed active.

    Raises:
        HTTPException(404) — slug not found, inactive, or no active location.
    """
    _slug = slug.lower().strip()

    # Step 1: Location.slug (canonical per OD-04 / ADR-002)
    loc = (
        db.query(Location)
        .filter(
            Location.slug == _slug,
            Location.is_active == True,
        )
        .first()
    )

    if loc is not None:
        restaurant = db.query(Restaurant).filter(
            Restaurant.id == loc.restaurant_id,
            Restaurant.is_active == True,
        ).first()
        if not restaurant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
        return restaurant, loc

    # Step 2: Restaurant.slug fallback
    restaurant = db.query(Restaurant).filter(
        Restaurant.slug == _slug,
        Restaurant.is_active == True,
    ).first()
    if not restaurant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    # Correction 2: must have an active Location for web ordering
    loc = (
        db.query(Location)
        .filter(
            Location.restaurant_id == restaurant.id,
            Location.is_active == True,
        )
        .order_by(Location.id)
        .first()
    )
    if loc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    return restaurant, loc


# ─────────────────────────────────────────────────────────────────────────────
# GET /r/{slug} — Public SSR restaurant page (Phase 13)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/r/{slug}", response_class=HTMLResponse)
@limiter.limit("120/minute")
def public_restaurant_page(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),
):
    """
    GET /r/{slug} — Server-rendered public restaurant ordering page.

    OD-01: Jinja2 SSR for SEO foundations.
    OD-04: Location.slug canonical; Restaurant.slug fallback supported.
    Correction 2: HTTP 404 when Restaurant has no active Location.

    The template renders SEO meta server-side. JavaScript hydrates
    menu/cart/checkout using the existing GET /api/restaurants/{slug} API.
    """
    restaurant, location = resolve_slug(slug, db)

    # Canonical URL always uses Location.slug (OD-04)
    canonical_url = f"{settings.PUBLIC_BASE_URL}/r/{location.slug}"

    # OG image: logo_url if set, else omit (Jinja2 checks for None)
    og_image: Optional[str] = restaurant.logo_url or None

    # SEO description: use Restaurant.description if available, else welcome_text
    # Correction 4: Restaurant.description exists (confirmed in models/tenant.py:89)
    seo_description: str = (
        restaurant.description
        or restaurant.welcome_text
        or restaurant.name
    )

    return templates.TemplateResponse(
        "web_restaurant.html",
        {
            "request": request,           # required by Jinja2Templates
            "restaurant": restaurant,
            "location": location,
            "canonical_url": canonical_url,
            "og_image": og_image,
            "seo_description": seo_description,
            # Pass slug for JS bootstrap — always use location.slug (canonical)
            "canonical_slug": location.slug,
        },
    )
