"""
routers/superadmin.py — Taomly Platform
Super Admin Console: управление всей платформой.

Слой 1:
  - Dashboard: агентства, рестораны, MRR, статистика
  - Управление агентствами: CRUD, блокировка, просмотр ресторанов, impersonate
  - Управление ресторанами: список всех, поиск, фильтр, заморозка, перенос

Изменения v7 (Security Patch):
  - Пароль суперадмина верифицируется через bcrypt.verify() (не plaintext compare_digest)
  - Все endpoints используют Pydantic-схемы вместо data: dict
  - N+1 устранён в list_agencies и get_dashboard через JOIN
  - Rate limit 5/minute на /login

Изменения v8 (Performance Patch):
  - get_dashboard: 7 отдельных COUNT/scalar запросов → 2 запроса через CASE WHEN.
    Агентства: total, active, new_this_month — один SELECT.
    Рестораны: total, active, new_this_month — один SELECT.
    Экономия: 5 round-trip к Neon на каждый вызов dashboard.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from auth import create_agency_token, hash_password
from config import settings
from database import get_db
from limiter import limiter
from models import Agency, Restaurant, Subscription, SubscriptionPlan

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ──────────────────────────────────────────
# СХЕМЫ SUPERADMIN (валидация входных данных)
# ──────────────────────────────────────────

class SuperAdminLogin(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=256)


class SuperAdminAgencyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class SuperAdminAgencyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=8, max_length=128)
    is_active: Optional[bool] = None


class SuperAdminFreezeRequest(BaseModel):
    is_active: bool


class SuperAdminTransferRequest(BaseModel):
    agency_id: int = Field(..., gt=0)


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
def superadmin_login(data: SuperAdminLogin, request: Request):
    """
    Вход суперадмина.

    Безопасность:
      - Пароль верифицируется через bcrypt.verify() — хэш в env, не plaintext.
      - Email сравнивается через hmac.compare_digest() — защита от timing attack.
      - bcrypt.verify всегда выполняется (даже при неверном email) — timing-safe.
      - Rate limit 5/minute — защита от brute force.
    """
    import hmac as _hmac

    incoming_email = data.email
    incoming_password = data.password

    email_ok = _hmac.compare_digest(incoming_email, settings.SUPERADMIN_EMAIL)
    # bcrypt.verify выполняется всегда — даже если email неверен (timing safety)
    password_ok = _pwd_ctx.verify(incoming_password, settings.SUPERADMIN_PASSWORD_HASH)

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
    """
    Главные метрики платформы.

    Оптимизация (v8): вместо 7 отдельных COUNT-запросов —
    2 запроса с CASE WHEN. Каждый возвращает total, active, new_this_month
    за один round-trip к БД.
    """
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── Запрос 1: все метрики по агентствам за один SELECT ──
    agency_stats = db.query(
        func.count(Agency.id).label("total"),
        func.sum(
            case((Agency.is_active == True, 1), else_=0)
        ).label("active"),
        func.sum(
            case((Agency.created_at >= month_start, 1), else_=0)
        ).label("new_this_month"),
    ).one()

    total_agencies      = agency_stats.total or 0
    active_agencies     = int(agency_stats.active or 0)
    new_agencies_month  = int(agency_stats.new_this_month or 0)

    # ── Запрос 2: все метрики по ресторанам за один SELECT ──
    restaurant_stats = db.query(
        func.count(Restaurant.id).label("total"),
        func.sum(
            case((Restaurant.is_active == True, 1), else_=0)
        ).label("active"),
        func.sum(
            case((Restaurant.created_at >= month_start, 1), else_=0)
        ).label("new_this_month"),
    ).one()

    total_restaurants      = restaurant_stats.total or 0
    active_restaurants     = int(restaurant_stats.active or 0)
    new_restaurants_month  = int(restaurant_stats.new_this_month or 0)

    # ── Запрос 3: MRR через JOIN подписок ──
    mrr_result = (
        db.query(func.sum(SubscriptionPlan.price))
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .filter(Subscription.is_active == True)
        .scalar()
    ) or 0

    # ── Запрос 4: последние 5 агентств + количество ресторанов (один JOIN) ──
    recent_agencies_rows = (
        db.query(Agency, func.count(Restaurant.id).label("restaurant_count"))
        .outerjoin(Restaurant, Restaurant.agency_id == Agency.id)
        .group_by(Agency.id)
        .order_by(Agency.created_at.desc())
        .limit(5)
        .all()
    )

    # ── Запрос 5: последние 5 ресторанов ──
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
                "restaurant_count": cnt,
            }
            for a, cnt in recent_agencies_rows
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

    # JOIN вместо N+1: один запрос для всех агентств + их restaurant_count
    agencies_with_count = (
        q.outerjoin(Restaurant, Restaurant.agency_id == Agency.id)
        .with_entities(Agency, func.count(Restaurant.id).label("restaurant_count"))
        .group_by(Agency.id)
        .order_by(Agency.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "items": [
            {
                "id": a.id,
                "name": a.name,
                "email": a.owner_email,
                "is_active": a.is_active,
                "created_at": a.created_at.isoformat(),
                "restaurant_count": cnt,
            }
            for a, cnt in agencies_with_count
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
    data: SuperAdminAgencyCreate,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    if db.query(Agency).filter(Agency.owner_email == data.email).first():
        raise HTTPException(status_code=400, detail="Email уже занят")

    agency = Agency(
        name=data.name,
        owner_email=str(data.email),
        owner_password_hash=hash_password(data.password),
    )
    db.add(agency)
    db.commit()
    db.refresh(agency)
    logger.info("Superadmin создал агентство id=%s", agency.id)
    return {"id": agency.id, "name": agency.name, "email": agency.owner_email}


@router.patch("/agencies/{agency_id}")
def update_agency(
    agency_id: int,
    data: SuperAdminAgencyUpdate,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    agency = db.query(Agency).filter(Agency.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Агентство не найдено")

    if data.name is not None:
        agency.name = data.name
    if data.email is not None:
        agency.owner_email = str(data.email)
    if data.password is not None:
        agency.owner_password_hash = hash_password(data.password)
    if data.is_active is not None:
        agency.is_active = data.is_active

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
    data: SuperAdminFreezeRequest,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    restaurant.is_active = data.is_active
    db.commit()
    action = "разморожен" if restaurant.is_active else "заморожен"
    logger.info("Superadmin: ресторан id=%s %s", restaurant_id, action)
    return {"ok": True, "is_active": restaurant.is_active}


@router.patch("/restaurants/{restaurant_id}/transfer")
def transfer_restaurant(
    restaurant_id: int,
    data: SuperAdminTransferRequest,
    _=Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    new_agency = db.query(Agency).filter(Agency.id == data.agency_id).first()
    if not new_agency:
        raise HTTPException(status_code=404, detail="Агентство не найдено")

    old_agency_id = restaurant.agency_id
    restaurant.agency_id = data.agency_id
    db.commit()
    logger.info(
        "Superadmin: ресторан id=%s перенесён из agency=%s в agency=%s",
        restaurant_id, old_agency_id, data.agency_id,
    )
    return {"ok": True, "restaurant_id": restaurant_id, "new_agency_id": data.agency_id}
