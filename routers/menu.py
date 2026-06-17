from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List

from database import get_db
from models import Restaurant, Category, Product
from schemas import CategoryResponse

router = APIRouter()


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

    # Фильтруем только доступные продукты и сортируем
    for category in categories:
        category.products = sorted(
            [p for p in category.products if p.is_available],
            key=lambda p: p.sort_order
        )

    # Убираем пустые категории
    categories = [c for c in categories if c.products]

    return categories
