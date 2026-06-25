from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Agency, Restaurant
from schemas import (
    AgencyLogin, AgencyRegister, AgencyResponse,
    RestaurantCreate, RestaurantUpdate, RestaurantAdminResponse,
    RestaurantAdminLogin, TokenResponse
)
from auth import (
    hash_password, verify_password,
    create_agency_token, create_restaurant_token,
    get_current_agency, encrypt_token
)

router = APIRouter(prefix="/api/agency", tags=["agency"])


# ──────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────
@router.post("/register", response_model=AgencyResponse)
def register_agency(data: AgencyRegister, db: Session = Depends(get_db)):
    existing = db.query(Agency).filter(Agency.owner_email == data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")

    agency = Agency(
        name=data.name,
        owner_email=data.email,
        owner_password_hash=hash_password(data.password),
    )
    db.add(agency)
    db.commit()
    db.refresh(agency)
    return agency


@router.post("/login", response_model=TokenResponse)
def login_agency(data: AgencyLogin, db: Session = Depends(get_db)):
    agency = db.query(Agency).filter(
        Agency.owner_email == data.email,
        Agency.is_active == True
    ).first()

    if not agency or not verify_password(data.password, agency.owner_password_hash):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    return TokenResponse(access_token=create_agency_token(agency))


@router.post("/restaurant-login", response_model=TokenResponse)
def login_restaurant_admin(data: RestaurantAdminLogin, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(
        Restaurant.slug == data.slug.lower().strip(),
        Restaurant.is_active == True
    ).first()

    if not restaurant or not restaurant.admin_password_hash:
        raise HTTPException(status_code=401, detail="Ресторан не найден")

    if not verify_password(data.password, restaurant.admin_password_hash):
        raise HTTPException(status_code=401, detail="Неверный пароль")

    return TokenResponse(access_token=create_restaurant_token(restaurant))


# ──────────────────────────────────────────
# РЕСТОРАНЫ — Agency Owner CRUD
# ──────────────────────────────────────────
@router.post("/restaurants", response_model=RestaurantAdminResponse)
def create_restaurant(
    data: RestaurantCreate,
    db: Session = Depends(get_db),
    agency: Agency = Depends(get_current_agency)
):
    slug = data.slug.lower().strip()

    existing = db.query(Restaurant).filter(Restaurant.slug == slug).first()
    if existing:
        raise HTTPException(status_code=400, detail="Slug уже занят")

    custom_domain = None
    if data.custom_domain:
        custom_domain = data.custom_domain.strip().lower()
        domain_exists = db.query(Restaurant).filter(
            Restaurant.custom_domain == custom_domain
        ).first()
        if domain_exists:
            raise HTTPException(status_code=400, detail="Домен уже занят")

    encrypted_token = None
    if data.telegram_bot_token:
        encrypted_token = encrypt_token(data.telegram_bot_token)

    restaurant = Restaurant(
        agency_id=agency.id,
        name=data.name,
        slug=slug,
        description=data.description,
        phone=data.phone,
        address=data.address,
        admin_password_hash=hash_password(data.admin_password),
        logo_url=data.logo_url,
        primary_color=data.primary_color or "#8B1A2E",
        secondary_color=data.secondary_color or "#FAF6EE",
        accent_color=data.accent_color or "#D4A853",
        welcome_text=data.welcome_text,
        custom_domain=custom_domain,
        telegram_bot_token_encrypted=encrypted_token,
        telegram_dispatcher_id=data.telegram_dispatcher_id,
    )
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


@router.get("/restaurants", response_model=list[RestaurantAdminResponse])
def get_restaurants(
    db: Session = Depends(get_db),
    agency: Agency = Depends(get_current_agency)
):
    return db.query(Restaurant).filter(
        Restaurant.agency_id == agency.id
    ).order_by(Restaurant.created_at.desc()).all()


@router.get("/restaurants/{restaurant_id}", response_model=RestaurantAdminResponse)
def get_restaurant(
    restaurant_id: int,
    db: Session = Depends(get_db),
    agency: Agency = Depends(get_current_agency)
):
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.agency_id == agency.id
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")
    return restaurant


@router.patch("/restaurants/{restaurant_id}", response_model=RestaurantAdminResponse)
def update_restaurant(
    restaurant_id: int,
    data: RestaurantUpdate,
    db: Session = Depends(get_db),
    agency: Agency = Depends(get_current_agency)
):
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.agency_id == agency.id
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    if data.custom_domain:
        custom_domain = data.custom_domain.strip().lower()
        if custom_domain != restaurant.custom_domain:
            domain_exists = db.query(Restaurant).filter(
                Restaurant.custom_domain == custom_domain
            ).first()
            if domain_exists:
                raise HTTPException(status_code=400, detail="Домен уже занят")
            data.custom_domain = custom_domain

    update_fields = data.model_dump(exclude_none=True)

    if "admin_password" in update_fields:
        restaurant.admin_password_hash = hash_password(update_fields.pop("admin_password"))

    if "telegram_bot_token" in update_fields:
        restaurant.telegram_bot_token_encrypted = encrypt_token(
            update_fields.pop("telegram_bot_token")
        )

    for field, value in update_fields.items():
        setattr(restaurant, field, value)

    db.commit()
    db.refresh(restaurant)
    return restaurant


@router.delete("/restaurants/{restaurant_id}")
def delete_restaurant(
    restaurant_id: int,
    db: Session = Depends(get_db),
    agency: Agency = Depends(get_current_agency)
):
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.agency_id == agency.id
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    restaurant.is_active = False
    db.commit()
    return {"ok": True, "detail": "Ресторан деактивирован"}
