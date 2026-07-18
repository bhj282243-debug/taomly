"""
routers/superadmin.py — Taomly Platform
Super Admin Console: управление всей платформой.

Слой 1:
  - Dashboard: агентства, рестораны, MRR, статистика
  - Управление агентствами: CRUD, блокировка, просмотр ресторанов, impersonate
  - Управление ресторанами: список всех, поиск, фильтр, заморозка, перенос

Изменения v6 (Security Patch C-1, C-2, C-3):
  C-1: hmac.compare_digest() вместо != для сравнения пароля суперадмина
       — устраняет timing attack.
  C-2: @limiter.limit("5/minute") на /login
       — brute force теперь ограничен.
  C-3: SUPERADMIN_EMAIL и SUPERADMIN_PASSWORD читаются из settings
       — убран прямой os.getenv(), убран import os.
"""

import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from auth import create_agency_token, hash_password
from config import settings
from database import get_db
from limiter import limiter
from models import Agency, Restaurant, Subscription, SubscriptionPlan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/superadmin", tags=["superadmin"])

ALGORITHM = "HS256"
SUPERADMIN_ROLE = "superadmin"


# ──────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────

def _create_superadmin_token() -> str:
    payload = {
        "role": SUPERADMIN_ROLE,
        "exp": datetime.now(timezone.utc) + timedelta(hours=12),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def get_current_superadmin(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не авторизован")
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительный токен")
    if payload.get("role") != SUPERADMIN_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    return payload


@router.post("/login")
@limiter.limit(settings.RATE_LIMIT_SUPERADMIN_LOGIN)
def superadmin_login(data: dict, request: Request):
    """
    Вход суперадмина.

    Безопасность:
      - Пароль сравнивается через hmac.compare_digest() — защита от timing attack (C-1).
      - Rate limit 5/minute — защита от brute force (C-2).
      - Credentials читаются из settings (config.py), не из os.getenv (C-3).
    """
    incoming_email = data.get("email", "")
    incoming_password = data.get("password", "")

    # hmac.compare_digest требует строки одинакового типа.
    # Всегда сравниваем оба поля — даже если email не совпал,
    # чтобы не давать информацию по времени ответа.
    email_ok = hmac.compare_digest(incoming_email, settings.SUPERADMIN_EMAIL)
    password_ok = hmac.compare_digest(incoming_password, settings.SUPERADMIN_PASSWORD)

    if not email_ok or not password_ok:
        logger.warning(
            "Superadmin login failed: неверные credentials от IP %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный email или пароль")

    logger.info("Superadmin вошёл с IP: %s", request.client.host if request.client else "unknown")
    return {"access_token": _create_superadmin_token()}


# ──────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────

@router.get("/dashboard")
def get_dashboard(
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    """Главные метрики платформы."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_agencies = db.query(func.count(Agency.id)).scalar()
    active_agencies = db.query(func.count(Agency.id)).filter(Agency.is_active == True).scalar()

    total_restaurants = db.query(func.count(Restaurant.id)).scalar()
    active_restaurants = db.query(func.count(Restaurant.id)).filter(Restaurant.is_active == True).scalar()

    new_agencies_month = db.query(func.count(Agency.id)).filter(
        Agency.created_at >= month_start
    ).scalar()

    new_restaurants_month = db.query(func.count(Restaurant.id)).filter(
        Restaurant.created_at >= month_start
    ).scalar()

    mrr_result = (
        db.query(func.sum(SubscriptionPlan.price))
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .filter(Subscription.is_active == True)
        .scalar()
    ) or 0

    recent_agencies = (
        db.query(Agency)
        .order_by(Agency.created_at.desc())
        .limit(5)
        .all()
    )

    recent_restaurants = (
        db.query(Restaurant)
        .order_by(Restaurant.created_at.desc())
        .limit(5)
        .all()
    )

    return {
        "agencies": {
            "total": total_agencies,
            "active": active_agencies,
            "inactive": total_agencies - active_agencies,
            "new_this_month": new_agencies_month,
        },
        "restaurants": {
            "total": total_restaurants,
            "active": active_restaurants,
            "inactive": total_restaurants - active_restaurants,
            "new_this_month": new_restaurants_month,
        },
        "mrr": mrr_result,
        "arr": mrr_result * 12,
        "recent_agencies": [
            {
                "id": a.id,
                "name": a.name,
                "email": a.owner_email,
                "is_active": a.is_active,
                "created_at": a.created_at.isoformat(),
                "restaurant_count": db.query(func.count(Restaurant.id))
                    .filter(Restaurant.agency_id == a.id)
                    .scalar(),
            }
            for a in recent_agencies
        ],
        "recent_restaurants": [
            {
                "id": r.id,
                "name": r.name,
                "slug": r.slug,
                "is_active": r.is_active,
                "agency_id": r.agency_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in recent_restaurants
        ],
    }


# ──────────────────────────────────────────
# АГЕНТСТВА
# ──────────────────────────────────────────

@router.get("/agencies")
def list_agencies(
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    q = db.query(Agency)
    if search:
        q = q.filter(
            Agency.name.ilike(f"%{search}%") |
            Agency.owner_email.ilike(f"%{search}%")
        )
    if is_active is not None:
        q = q.filter(Agency.is_active == is_active)

    total = q.count()
    agencies = q.order_by(Agency.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": a.id,
                "name": a.name,
                "email": a.owner_email,
                "is_active": a.is_active,
                "created_at": a.created_at.isoformat(),
                "restaurant_count": db.query(func.count(Restaurant.id))
                    .filter(Restaurant.agency_id == a.id)
                    .scalar(),
            }
            for a in agencies
        ],
    }


@router.get("/agencies/{agency_id}")
def get_agency(
    agency_id: int,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    agency = db.query(Agency).filter(Agency.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Агентство не найдено")

    restaurants = db.query(Restaurant).filter(Restaurant.agency_id == agency_id).all()

    return {
        "id": agency.id,
        "name": agency.name,
        "email": agency.owner_email,
        "is_active": agency.is_active,
        "created_at": agency.created_at.isoformat(),
        "restaurants": [
            {
                "id": r.id,
                "name": r.name,
                "slug": r.slug,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat(),
            }
            for r in restaurants
        ],
    }


@router.post("/agencies", status_code=201)
def create_agency(
    data: dict,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    if not data.get("name") or not data.get("email") or not data.get("password"):
        raise HTTPException(status_code=400, detail="name, email и password обязательны")

    if db.query(Agency).filter(Agency.owner_email == data["email"]).first():
        raise HTTPException(status_code=400, detail="Email уже занят")

    agency = Agency(
        name=data["name"],
        owner_email=data["email"],
        owner_password_hash=hash_password(data["password"]),
    )
    db.add(agency)
    db.commit()
    db.refresh(agency)
    logger.info("Superadmin создал агентство id=%s", agency.id)
    return {"id": agency.id, "name": agency.name, "email": agency.owner_email}


@router.patch("/agencies/{agency_id}")
def update_agency(
    agency_id: int,
    data: dict,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    agency = db.query(Agency).filter(Agency.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Агентство не найдено")

    if "name" in data:
        agency.name = data["name"]
    if "email" in data:
        agency.owner_email = data["email"]
    if "password" in data:
        agency.owner_password_hash = hash_password(data["password"])
    if "is_active" in data:
        agency.is_active = data["is_active"]

    db.commit()
    db.refresh(agency)
    logger.info("Superadmin обновил агентство id=%s", agency_id)
    return {"ok": True, "id": agency.id, "is_active": agency.is_active}


@router.post("/agencies/{agency_id}/impersonate")
def impersonate_agency(
    agency_id: int,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    agency = db.query(Agency).filter(Agency.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Агентство не найдено")

    token = create_agency_token(agency)
    logger.warning("Superadmin impersonate agency_id=%s", agency_id)
    return {"access_token": token, "agency_name": agency.name}


# ──────────────────────────────────────────
# РЕСТОРАНЫ
# ──────────────────────────────────────────

@router.get("/restaurants")
def list_restaurants(
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    agency_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    q = db.query(Restaurant)
    if search:
        q = q.filter(
            Restaurant.name.ilike(f"%{search}%") |
            Restaurant.slug.ilike(f"%{search}%")
        )
    if is_active is not None:
        q = q.filter(Restaurant.is_active == is_active)
    if agency_id is not None:
        q = q.filter(Restaurant.agency_id == agency_id)

    total = q.count()
    restaurants = q.order_by(Restaurant.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "slug": r.slug,
                "address": r.address,
                "is_active": r.is_active,
                "agency_id": r.agency_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in restaurants
        ],
    }


@router.patch("/restaurants/{restaurant_id}/freeze")
def freeze_restaurant(
    restaurant_id: int,
    data: dict,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    restaurant.is_active = data.get("is_active", False)
    db.commit()
    action = "разморожен" if restaurant.is_active else "заморожен"
    logger.info("Superadmin: ресторан id=%s %s", restaurant_id, action)
    return {"ok": True, "is_active": restaurant.is_active}


@router.patch("/restaurants/{restaurant_id}/transfer")
def transfer_restaurant(
    restaurant_id: int,
    data: dict,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    new_agency_id = data.get("agency_id")
    if not new_agency_id:
        raise HTTPException(status_code=400, detail="agency_id обязателен")

    new_agency = db.query(Agency).filter(Agency.id == new_agency_id).first()
    if not new_agency:
        raise HTTPException(status_code=404, detail="Агентство не найдено")

    old_agency_id = restaurant.agency_id
    restaurant.agency_id = new_agency_id
    db.commit()
    logger.info(
        "Superadmin: ресторан id=%s перенесён из agency=%s в agency=%s",
        restaurant_id, old_agency_id, new_agency_id
    )
    return {"ok": True, "restaurant_id": restaurant_id, "new_agency_id": new_agency_id}
