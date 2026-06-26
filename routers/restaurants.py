from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models import Restaurant, Category, Product, RestaurantTable

router = APIRouter(prefix="/api/restaurants", tags=["restaurants"])


@router.get("/{slug}")
def get_restaurant_by_slug(slug: str, db: Session = Depends(get_db)):
    # 1. Ищем ресторан в базе данных по его текстовому адресу (slug)
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.slug == slug, Restaurant.is_active == True)
        .first()
    )
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    # 2. Загружаем категории меню и продукты для этого ресторана
    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant.id)
        .options(joinedload(Category.products))
        .order_by(Category.sort_order)
        .all()
    )

    # 3. Отдаем ВСЕ данные, включая логотип и фирменные цвета для White Label
    return {
        "id": restaurant.id,
        "name": restaurant.name,
        "slug": restaurant.slug,
        "description": restaurant.description,
        "phone": restaurant.phone,
        "address": restaurant.address,
        "is_waiter_call_enabled": restaurant.is_waiter_call_enabled,
        
        # 🌟 Новые поля, которые мы добавили для кастомизации:
        "logo_url": restaurant.logo_url,
        "primary_color": restaurant.primary_color,
        "secondary_color": restaurant.secondary_color,
        "accent_color": restaurant.accent_color,
        "welcome_text": restaurant.welcome_text,
        "custom_domain": restaurant.custom_domain,
        
        "categories": [
            {
                "id": cat.id,
                "name": cat.name,
                "sort_order": cat.sort_order,
                "products": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "price": p.price,
                        "photo_url": p.photo_url,
                        "is_available": p.is_available,
                        "sort_order": p.sort_order,
                    }
                    for p in sorted(cat.products, key=lambda x: x.sort_order)
                    if p.is_available
                ],
            }
            for cat in categories
        ],
    }


@router.get("/{slug}/table/{table_number}")
def get_table_by_number(slug: str, table_number: str, db: Session = Depends(get_db)):
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.slug == slug, Restaurant.is_active == True)
        .first()
    )
    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    table = (
        db.query(RestaurantTable)
        .filter(
            RestaurantTable.restaurant_id == restaurant.id,
            RestaurantTable.table_number == table_number
        )
        .first()
    )
    if not table:
        raise HTTPException(status_code=404, detail="Stol topilmadi")

    return {
        "restaurant_id": restaurant.id,
        "restaurant_name": restaurant.name,
        "slug": restaurant.slug,
        "table_id": table.id,
        "table_number": table.table_number,
    }
