from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from pydantic import BaseModel

from database import get_db
from models import Restaurant, Category, Product
from schemas import CategoryResponse, ProductResponse

router = APIRouter()


# ─── SCHEMAS ─────────────────────────────────────────────────

class ProductCreate(BaseModel):
    category_id: int
    name: str
    price: int
    description: Optional[str] = None
    photo_url: Optional[str] = None
    is_available: bool = True
    sort_order: int = 0


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[int] = None
    description: Optional[str] = None
    photo_url: Optional[str] = None
    is_available: Optional[bool] = None
    sort_order: Optional[int] = None
    category_id: Optional[int] = None


# ─── GET MENU (клиент — только доступные) ────────────────────

@router.get("/{restaurant_id}", response_model=List[CategoryResponse])
def get_menu(restaurant_id: int, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.is_active == True
    ).first()

    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant_id)
        .options(joinedload(Category.products))
        .order_by(Category.sort_order)
        .all()
    )

    for c in categories:
        c.products = sorted(
            [p for p in (c.products or []) if p.is_available],
            key=lambda p: p.sort_order
        )

    return [c for c in categories if c.products]


# ─── GET ALL PRODUCTS (админка — включая выключенные) ────────

@router.get("/{restaurant_id}/all", response_model=List[CategoryResponse])
def get_menu_all(restaurant_id: int, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.is_active == True
    ).first()

    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant_id)
        .options(joinedload(Category.products))
        .order_by(Category.sort_order)
        .all()
    )

    for c in categories:
        c.products = sorted(
            c.products or [],
            key=lambda p: p.sort_order
        )

    return [c for c in categories if c.products]


# ─── CREATE PRODUCT ──────────────────────────────────────────

@router.post("/product/", response_model=ProductResponse)
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    if data.price <= 0:
        raise HTTPException(status_code=400, detail="Narx noto'g'ri")

    category = db.query(Category).filter(Category.id == data.category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Kategoriya topilmadi")

    product = Product(
        restaurant_id=category.restaurant_id,
        category_id=data.category_id,
        name=data.name,
        price=data.price,
        description=data.description,
        photo_url=data.photo_url,
        is_available=data.is_available,
        sort_order=data.sort_order,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


# ─── UPDATE PRODUCT ──────────────────────────────────────────

@router.patch("/product/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, data: ProductUpdate, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")

    if data.price is not None and data.price <= 0:
        raise HTTPException(status_code=400, detail="Narx noto'g'ri")

    if data.category_id is not None:
        category = db.query(Category).filter(Category.id == data.category_id).first()
        if not category:
            raise HTTPException(status_code=404, detail="Kategoriya topilmadi")
        product.category_id = data.category_id

    if data.name is not None:
        product.name = data.name
    if data.price is not None:
        product.price = data.price
    if data.description is not None:
        product.description = data.description
    if data.photo_url is not None:
        product.photo_url = data.photo_url
    if data.is_available is not None:
        product.is_available = data.is_available
    if data.sort_order is not None:
        product.sort_order = data.sort_order

    db.commit()
    db.refresh(product)
    return product
